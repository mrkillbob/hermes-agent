"""Discord voice packet validation, transport decryption and PCM decoding."""
from __future__ import annotations

import struct
import time


class VoicePacketMixin:
    """Packet phases sharing the receiver's lock, credential tuple and PCM buffers."""

    def _parse_packet_header(self, data: bytes):
        """Reject non-voice RTP and return its bounded transport header fields."""
        _, logger = self._voice_packet_dependencies()

        if len(data) < 16:
            return None
        # RTP v2: top 2 bits 10 (rest varies); voice payload type (byte 1 & 0x7F) is 0x78.
        if (data[0] >> 6) != 2 or (data[1] & 0x7F) != 0x78:
            if self._packet_debug_count <= 5:
                logger.debug("Skipped non-RTP: byte0=0x%02x byte1=0x%02x", data[0], data[1])
            return None
        first_byte = data[0]
        _, _, seq, _timestamp, ssrc = struct.unpack_from(">BBHII", data, 0)
        if ssrc == self._bot_ssrc:
            return None
        # Calculate dynamic RTP header size (RFC 9335 / rtpsize mode)
        cc = first_byte & 0x0F  # CSRC count
        has_extension = bool(first_byte & 0x10)  # extension bit
        has_padding = bool(first_byte & 0x20)  # padding bit (RFC 3550 §5.1)
        header_size = 12 + (4 * cc) + (4 if has_extension else 0)
        if len(data) < header_size + 4:  # need at least header + nonce
            return None
        # Read extension length from preamble (for skipping after decrypt)
        ext_data_len = 0
        if has_extension:
            ext_preamble_offset = 12 + (4 * cc)
            ext_words = struct.unpack_from(">H", data, ext_preamble_offset + 2)[0]
            ext_data_len = ext_words * 4
        if self._packet_debug_count <= 10:
            with self._lock:
                known_user = self._ssrc_to_user.get(ssrc, "unknown")
            logger.debug(
                "RTP packet: ssrc=%d, seq=%d, user=%s, hdr=%d, ext_data=%d",
                ssrc, seq, known_user, header_size, ext_data_len,
            )
        header = bytes(data[:header_size])
        payload_with_nonce = data[header_size:]
        # --- NaCl transport decrypt (aead_xchacha20_poly1305_rtpsize) ---
        if len(payload_with_nonce) < 4:
            return None
        return ssrc, header, ext_data_len, has_padding, payload_with_nonce

    def _strip_packet_padding(self, decrypted: bytes, ssrc: int):
        """Strip valid RTP padding, or drop empty/corrupt voice payloads."""
        _, logger = self._voice_packet_dependencies()

        if not decrypted:
            if self._packet_debug_count <= 10:
                logger.warning("RTP padding bit set but no payload (ssrc=%d)", ssrc)
            return None
        pad_len = decrypted[-1]
        if pad_len == 0 or pad_len > len(decrypted):
            if self._packet_debug_count <= 10:
                logger.warning(
                    "Invalid RTP padding length %d for payload size %d (ssrc=%d)",
                    pad_len, len(decrypted), ssrc,
                )
            return None
        decrypted = decrypted[:-pad_len]
        if not decrypted:
            return None
        return decrypted

    def _on_packet(self, data: bytes):
        if not self._running or self._paused:
            return

        discord, logger = self._voice_packet_dependencies()

        # One consistent credential generation for this packet — a refresh
        # on another thread swaps the whole tuple, never half of it.
        secret_key, dave_session, dave_pver, _dave_downgraded = self._creds

        # Log first few raw packets for debugging
        self._packet_debug_count += 1
        if self._packet_debug_count <= 5:
            logger.debug(
                "Raw UDP packet: len=%d, first_bytes=%s",
                len(data), data[:4].hex() if len(data) >= 4 else "short",
            )
        parsed = self._parse_packet_header(data)
        if parsed is None:
            return
        ssrc, header, ext_data_len, has_padding, payload_with_nonce = parsed
        header_size = len(header)
        nonce = bytearray(24)
        nonce[:4] = payload_with_nonce[-4:]
        encrypted = bytes(payload_with_nonce[:-4])
        try:
            import nacl.secret  # noqa: E402 — delayed import, only in voice path
            box = nacl.secret.Aead(secret_key)
            decrypted = box.decrypt(encrypted, header, bytes(nonce))
            self._nacl_fail_streak = 0
        except Exception as e:
            self._decode_failed += 1
            self._nacl_fail_streak += 1
            # Never go fully dark: after the first 10 warnings, keep emitting
            # one every 250 failures so a deaf session stays diagnosable.
            if self._packet_debug_count <= 10 or self._nacl_fail_streak % 250 == 0:
                logger.warning(
                    "NaCl decrypt failed: %s (hdr=%d, enc=%d, streak=%d)",
                    e, header_size, len(encrypted), self._nacl_fail_streak, exc_info=True,
                )
            # A sustained failure streak means the transport key rotated
            # under us (voice reconnect / re-key) — re-read it from the live
            # connection instead of staying deaf on a stale copy.  The
            # refresh resets the streak, so this retries every
            # REKEY_FAILURE_STREAK packets while the failure persists.
            if self._nacl_fail_streak >= self.REKEY_FAILURE_STREAK:
                self.refresh_credentials("decrypt-failure streak")
            return
        # Skip encrypted extension data to get the actual opus payload
        if ext_data_len and len(decrypted) > ext_data_len:
            decrypted = decrypted[ext_data_len:]
        # Strip RTP padding before either DAVE or Opus sees the voice payload.
        if has_padding:
            decrypted = self._strip_packet_padding(decrypted, ssrc)
            if decrypted is None:
                return
        # --- DAVE E2EE decrypt ---
        if dave_session:
            with self._lock:
                user_id = self._ssrc_to_user.get(ssrc, 0)
                if not user_id:
                    # Rejoin race: SPEAKING may never be resent for a user who
                    # was already talking — try the sole-member inference
                    # before giving up on this frame.
                    user_id = self._infer_user_for_ssrc(ssrc)
            if user_id:
                try:
                    import davey
                    decrypted = dave_session.decrypt(
                        user_id, davey.MediaType.audio, decrypted
                    )
                except Exception as e:
                    # Unencrypted passthrough — use NaCl-decrypted data as-is
                    if "Unencrypted" not in str(e):
                        if self._packet_debug_count <= 10:
                            logger.warning("DAVE decrypt failed for ssrc=%d: %s", ssrc, e, exc_info=True)
                        return
            elif (
                dave_pver > 0
                and time.monotonic() >= self._dave_passthrough_until
            ):
                # E2EE is actively on (protocol > 0, no passthrough window),
                # so an unmapped SSRC's payload is still ciphertext.  Opus
                # will happily "decode" it (producing shredded audio and
                # poisoning decoder state), so drop the frame until a
                # SPEAKING event maps the SSRC — bounded loss beats corrupt
                # audio.  A non-null session alone is NOT this predicate: the
                # session object survives protocol downgrades to 0 and
                # passthrough transitions, where plaintext is legitimate and
                # must fall through to opus below.
                self._dave_unmapped_dropped += 1
                if (
                    self._packet_debug_count <= 10
                    or self._dave_unmapped_dropped % 250 == 1
                ):
                    logger.debug(
                        "Dropping DAVE frame for unmapped ssrc=%d (dropped=%d)",
                        ssrc, self._dave_unmapped_dropped,
                    )
                return

        # --- Opus decode -> PCM ---
        try:
            if ssrc not in self._decoders:
                self._decoders[ssrc] = discord.opus.Decoder()
            pcm = self._decoders[ssrc].decode(decrypted)
            self._decode_ok += 1
            with self._lock:
                self._buffers[ssrc].extend(pcm)
                self._last_packet_time[ssrc] = time.monotonic()
        except Exception as e:
            with self._lock:
                self._decoders.pop(ssrc, None)
            logger.debug("Opus decode error for SSRC %s; reset decoder: %s", ssrc, e, exc_info=True)
            return
