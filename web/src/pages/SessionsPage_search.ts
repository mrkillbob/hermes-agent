import type { SessionSearchResult } from "@/lib/api";

/** Index search snippets by both the stored session identifier and the result row identifier. */
export function searchSnippetMap(
  searchResults: SessionSearchResult[] | null,
): Map<string, string> {
  const snippets = new Map<string, string>();
  if (searchResults) {
    for (const result of searchResults) {
      snippets.set(result.session_id, result.snippet);
      snippets.set(result.id, result.snippet);
    }
  }
  return snippets;
}
