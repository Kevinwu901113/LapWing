import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { ChevronRight, ChevronDown, FileText, Folder, Search, Brain } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getNotesTree, getNoteContent, searchNotes, recallNotes } from "@/lib/api-v2";
import type { NoteTreeEntry, NoteContent, NoteSearchResult } from "@/types/notes";

function TreeNode({ entry, depth, onSelect, selectedPath }: {
  entry: NoteTreeEntry;
  depth: number;
  onSelect: (path: string) => void;
  selectedPath: string | null;
}) {
  const [expanded, setExpanded] = useState(depth === 0);
  const isDir = entry.type === "directory";
  const isSelected = entry.path === selectedPath;

  return (
    <div>
      <button
        onClick={() => {
          if (isDir) setExpanded(!expanded);
          else onSelect(entry.path);
        }}
        className={`flex items-center gap-1.5 w-full text-left px-2 py-1 rounded text-xs transition-colors ${
          isSelected ? "bg-surface-active text-text-accent" : "text-text-secondary hover:bg-surface-hover hover:text-text-primary"
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {isDir ? (
          expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />
        ) : (
          <FileText size={12} className="text-text-muted" />
        )}
        {isDir && <Folder size={12} className="text-lapwing" />}
        <span className="truncate">{entry.name}</span>
      </button>
      {isDir && expanded && entry.children?.map((child) => (
        <TreeNode
          key={child.path}
          entry={child}
          depth={depth + 1}
          onSelect={onSelect}
          selectedPath={selectedPath}
        />
      ))}
    </div>
  );
}

export default function NotesPage() {
  const [tree, setTree] = useState<NoteTreeEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [note, setNote] = useState<NoteContent | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"keyword" | "semantic">("keyword");
  const [searchResults, setSearchResults] = useState<NoteSearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  const fetchTree = useCallback(async () => {
    try {
      const data = await getNotesTree();
      setTree(data.entries ?? []);
    } catch { /* offline */ }
  }, []);

  useEffect(() => { fetchTree(); }, [fetchTree]);

  const handleSelect = async (path: string) => {
    setSelectedPath(path);
    setSearchResults([]);
    try {
      const data = await getNoteContent({ path });
      setNote(data);
    } catch {
      setNote(null);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    setNote(null);
    setSelectedPath(null);
    try {
      const data = searchMode === "keyword"
        ? await searchNotes(searchQuery)
        : await recallNotes(searchQuery);
      setSearchResults(data.results ?? []);
    } catch {
      setSearchResults([]);
    }
    setSearching(false);
  };

  const handleSearchResultClick = async (result: NoteSearchResult) => {
    const path = result.file_path || result.note_id;
    if (!path) return;
    setSelectedPath(path);
    try {
      const data = await getNoteContent(result.note_id ? { note_id: result.note_id } : { path });
      setNote(data);
      setSearchResults([]);
    } catch {
      setNote(null);
    }
  };

  const hasMeta = note?.meta && Object.keys(note.meta).length > 0;

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">Notes</h1>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left: Tree + Search */}
        <div className="w-[240px] shrink-0 border-r border-surface-border flex flex-col">
          <ScrollArea className="flex-1">
            <div className="p-2">
              {tree.length === 0 ? (
                <div className="text-xs text-text-muted text-center py-4">No notes</div>
              ) : (
                tree.map((entry) => (
                  <TreeNode
                    key={entry.path}
                    entry={entry}
                    depth={0}
                    onSelect={handleSelect}
                    selectedPath={selectedPath}
                  />
                ))
              )}
            </div>
          </ScrollArea>

          {/* Search area */}
          <div className="border-t border-surface-border p-2 space-y-2">
            <div className="flex gap-1">
              <Button
                size="sm"
                variant={searchMode === "keyword" ? "default" : "outline"}
                onClick={() => setSearchMode("keyword")}
                className="flex-1 h-7 text-xs gap-1"
              >
                <Search size={10} /> Keyword
              </Button>
              <Button
                size="sm"
                variant={searchMode === "semantic" ? "default" : "outline"}
                onClick={() => setSearchMode("semantic")}
                className="flex-1 h-7 text-xs gap-1"
              >
                <Brain size={10} /> Semantic
              </Button>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); handleSearch(); }} className="flex gap-1">
              <Input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search notes..."
                className="bg-void-50 border-surface-border text-text-primary text-xs h-7"
              />
            </form>
          </div>
        </div>

        {/* Right: Content */}
        <div className="flex-1 min-w-0">
          <ScrollArea className="h-full">
            <div className="p-4">
              {searchResults.length > 0 && (
                <div className="space-y-2 mb-4">
                  <div className="text-xs text-text-muted">{searchResults.length} results</div>
                  {searchResults.map((r, i) => (
                    <button
                      key={i}
                      onClick={() => handleSearchResultClick(r)}
                      className="w-full text-left bg-surface border border-surface-border rounded-lg p-3 hover:bg-surface-hover transition-colors"
                    >
                      <div className="text-xs text-lapwing truncate">{r.file_path}</div>
                      <div className="text-xs text-text-secondary mt-1 line-clamp-2">{r.content}</div>
                      {r.score !== undefined && (
                        <div className="text-[10px] text-text-muted mt-1">score: {r.score.toFixed(3)}</div>
                      )}
                    </button>
                  ))}
                </div>
              )}

              {searching && (
                <div className="text-sm text-text-muted text-center py-8">Searching...</div>
              )}

              {note ? (
                <>
                  {hasMeta && (
                    <details className="mb-4">
                      <summary className="text-xs text-text-muted cursor-pointer hover:text-text-secondary">
                        Metadata
                      </summary>
                      <pre className="mt-1 text-xs text-text-secondary bg-void-50 rounded p-2 overflow-x-auto">
                        {JSON.stringify(note.meta, null, 2)}
                      </pre>
                    </details>
                  )}
                  <div className="text-xs text-text-muted mb-3">{note.file_path}</div>
                  <div className="prose prose-invert prose-sm max-w-none text-text-primary">
                    <Markdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        p: ({ children }) => <p className="mb-2 last:mb-0 text-sm text-text-primary">{children}</p>,
                        a: ({ href, children }) => (
                          <a href={href} className="text-lapwing hover:underline" target="_blank" rel="noopener noreferrer">{children}</a>
                        ),
                        pre: ({ children }) => (
                          <pre className="bg-void rounded p-2 my-2 overflow-x-auto text-xs">{children}</pre>
                        ),
                        code: ({ children }) => (
                          <code className="bg-void rounded px-1 py-0.5 text-xs">{children}</code>
                        ),
                        h1: ({ children }) => <h3 className="text-base font-semibold text-text-accent mb-1 mt-3">{children}</h3>,
                        h2: ({ children }) => <h3 className="text-sm font-semibold text-text-accent mb-1 mt-3">{children}</h3>,
                        h3: ({ children }) => <h4 className="text-sm font-medium text-text-accent mb-1 mt-2">{children}</h4>,
                        ul: ({ children }) => <ul className="list-disc pl-4 mb-2">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal pl-4 mb-2">{children}</ol>,
                        li: ({ children }) => <li className="mb-0.5 text-sm text-text-primary">{children}</li>,
                        blockquote: ({ children }) => (
                          <blockquote className="border-l-2 border-lapwing-border pl-3 my-2 text-text-secondary italic">{children}</blockquote>
                        ),
                      }}
                    >
                      {note.content}
                    </Markdown>
                  </div>
                </>
              ) : !searching && searchResults.length === 0 && (
                <div className="text-sm text-text-muted text-center py-12">
                  Select a note or search to begin
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
