import { useUser } from "@duar-auth/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { deleteNote, fetchNote, updateNote } from "../api/notes";
import { ShareDialog } from "../components/ShareDialog";

export function NoteDetail() {
  const { id } = useParams<{ id: string }>();
  const user = useUser();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: note, isLoading, error } = useQuery({
    queryKey: ["note", id],
    queryFn: () => fetchNote(id!),
    enabled: !!id,
  });

  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [showShare, setShowShare] = useState(false);

  const updateMutation = useMutation({
    mutationFn: () => updateNote(id!, { title, content }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["note", id] });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      setEditing(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteNote(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      navigate("/notes", { replace: true });
    },
  });

  const canDelete = ["admin", "owner"].includes(user.workspaceRole);
  const isOwner = note?.owner_id === user.userId;

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-12 text-center">
        <p className="mb-2 text-sm text-red-400">
          {error instanceof Error ? error.message : "Failed to load note"}
        </p>
        <p className="text-xs text-zinc-500">
          This may be a permission issue — the identity service's entity ACL
          denied access via <code>permissions.can()</code>.
        </p>
        <Link
          to="/notes"
          className="mt-4 inline-block text-sm text-zinc-400 underline"
        >
          Back to notes
        </Link>
      </div>
    );
  }

  if (!note) return null;

  function startEditing() {
    setTitle(note!.title);
    setContent(note!.content);
    setEditing(true);
  }

  return (
    <div>
      <Link
        to="/notes"
        className="mb-4 inline-block text-sm text-zinc-500 hover:text-zinc-300"
      >
        &larr; Back to notes
      </Link>

      {editing ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-6">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mb-3 w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 focus:border-zinc-500 focus:outline-none"
          />
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={8}
            className="mb-4 w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
          />
          <div className="flex gap-3">
            <button
              onClick={() => setEditing(false)}
              className="rounded bg-zinc-800 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-700"
            >
              Cancel
            </button>
            <button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
              className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
            >
              {updateMutation.isPending ? "Saving..." : "Save"}
            </button>
          </div>
          {updateMutation.isError && (
            <p className="mt-2 text-sm text-red-400">
              {updateMutation.error.message}
              <span className="block text-xs text-zinc-500">
                Edit requires entity-level 'edit' permission via{" "}
                <code>permissions.can()</code>
              </span>
            </p>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-6">
          <div className="mb-4 flex items-start justify-between">
            <h1 className="text-xl font-bold text-zinc-100">{note.title}</h1>
            <div className="flex gap-2">
              {isOwner && (
                <button
                  onClick={() => setShowShare(true)}
                  className="rounded bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700"
                >
                  Share
                </button>
              )}
              <button
                onClick={startEditing}
                className="rounded bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700"
              >
                Edit
              </button>
              {canDelete && (
                <button
                  onClick={() => {
                    if (confirm("Delete this note?"))
                      deleteMutation.mutate();
                  }}
                  disabled={deleteMutation.isPending}
                  className="rounded bg-red-600/20 px-3 py-1.5 text-xs text-red-400 hover:bg-red-600/30"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-300">
            {note.content}
          </p>
          <div className="mt-6 border-t border-zinc-800 pt-4 text-xs text-zinc-600">
            <p>
              by {note.owner_name} &middot;{" "}
              {new Date(note.created_at).toLocaleString()}
            </p>
          </div>
        </div>
      )}

      <div className="mt-4 rounded border border-zinc-800 bg-zinc-900/50 p-3 text-xs text-zinc-500">
        <strong>SDK features shown:</strong> Viewing uses{" "}
        <code>permissions.can(token, "note", id, "view")</code>. Editing uses{" "}
        <code>permissions.can(..., "edit")</code>. Deleting uses{" "}
        <code>require_role("admin")</code>. Sharing uses the permission
        service's share API.
      </div>

      {showShare && (
        <ShareDialog noteId={note.id} onClose={() => setShowShare(false)} />
      )}
    </div>
  );
}
