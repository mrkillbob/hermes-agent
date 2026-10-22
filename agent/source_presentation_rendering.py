"""Validate exact source presentations and produce their existing final scan inputs."""


def render_source_presentation(segment, grant_and_content):
    from agent.llm_egress_firewall import _source_text_for_secret_scan, _source_text_for_base64_scan
    from agent.source_context_presentation import source_presentation_matches
    from agent.source_context_rendering import (
        presentation_display_path, presentation_secret_text, presentation_path_encoding_inputs,
    )
    raw_text = grant_and_content[1].decode('utf-8')
    if not source_presentation_matches(segment, grant_and_content[0], raw_text):
        raise ValueError('invalid_source_presentation')
    return ([_source_text_for_secret_scan(raw_text),
             _source_text_for_secret_scan(presentation_secret_text(segment)), presentation_display_path(segment)],
            [*presentation_path_encoding_inputs(segment),
             _source_text_for_base64_scan(raw_text, allow_fixed_source_literals=True)])
