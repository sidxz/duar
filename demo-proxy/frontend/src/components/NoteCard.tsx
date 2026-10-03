import { Link } from "react-router-dom";
import type { Note } from "../api/notes";

export function NoteCard({
  note,
  isOwner,
}: {
  note: Note;
  isOwner: boolean;
}) {
  return (
    <Link
      to={`/notes/${note.id}`}
      className="block rounded-lg border border-zinc-800 bg-zinc-900 p-4 transition hover:border-zinc-700"
    >
      <div className="mb-2 flex items-start justify-between">
        <h3 className="font-medium text-zinc-100">{note.title}</h3>
        {isOwner && (
          <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-400">
            yours
          </span>
        )}
      </div>
      <p className="mb-3 line-clamp-2 text-sm text-zinc-400">{note.content}</p>
      <div className="text-xs text-zinc-600">
        by {note.owner_name} &middot;{" "}
        {new Date(note.created_at).toLocaleDateString()}
      </div>
    </Link>
  );
}
