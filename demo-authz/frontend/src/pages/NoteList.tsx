import { useAuthzUser } from "@duar-auth/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createNote, fetchNotes } from "../api/notes";
import { NoteCard } from "../components/NoteCard";

export function NoteList() {
  const user = useAuthzUser();
  const queryClient = useQueryClient();
  const canCreate = ["editor", "admin", "owner"].includes(user.workspaceRole);

  const { data: notes = [], isLoading } = useQuery({
    queryKey: ["notes"],
    queryFn: fetchNotes,
  });

  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  const mutation = useMutation({
    mutationFn: () => createNote(title, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      setTitle("");
      setContent("");
      setShowForm(false);
    },
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-zinc-100">Notes</h1>
        {canCreate && (
          <button
            onClick={() => setShowForm(!showForm)}
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500"
          >
            {showForm ? "Cancel" : "New Note"}
          </button>
        )}
      </div>

      {showForm && (
        <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900 p-4">
          <input
            type="text"
            placeholder="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mb-3 w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
          <textarea
            placeholder="Content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={4}
            className="mb-3 w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
          <button
            onClick={() => mutation.mutate()}
            disabled={!title || mutation.isPending}
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {mutation.isPending ? "Creating..." : "Create"}
          </button>
          {mutation.isError && (
            <p className="mt-2 text-sm text-red-400">
              {mutation.error.message}
            </p>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
        </div>
      ) : notes.length === 0 ? (
        <p className="py-12 text-center text-sm text-zinc-500">
          No notes yet.{" "}
          {canCreate
            ? "Create one to get started."
            : "Ask an editor to create one."}
        </p>
      ) : (
        <div className="grid gap-3">
          {notes.map((note) => (
            <NoteCard
              key={note.id}
              note={note}
              isOwner={note.owner_id === user.userId}
            />
          ))}
        </div>
      )}

      {!canCreate && (
        <div className="mt-8 rounded border border-zinc-800 bg-zinc-900/50 p-3 text-xs text-zinc-500">
          Your workspace role is <strong>{user.workspaceRole}</strong>. You
          need at least <strong>editor</strong> to create notes. This
          demonstrates <code>require_role("editor")</code> from the SDK.
        </div>
      )}
    </div>
  );
}
