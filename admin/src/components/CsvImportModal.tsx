import { useState } from "react";
import { Modal } from "./Modal";
import { Button } from "@/components/ui/button";
import { csvPreview, csvExecute } from "../api/client";
import type { CsvImportPreview, CsvImportResult } from "../types/api";

interface Props {
  open: boolean;
  onClose: () => void;
  onComplete: () => void;
}

export function CsvImportModal({ open, onClose, onComplete }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvImportPreview | null>(null);
  const [result, setResult] = useState<CsvImportResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handlePreview = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const data = await csvPreview(file);
      setPreview(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleExecute = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const data = await csvExecute(file);
      setResult(data);
      onComplete();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={open} onClose={handleClose} title="Import CSV">
      <div className="space-y-4">
        {!result ? (
          <>
            <div>
              <label className="text-xs text-muted-foreground">
                CSV with columns: email, name, workspace_slug, role
              </label>
              <input
                type="file"
                accept=".csv"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setPreview(null);
                }}
                className="mt-1 block w-full text-sm text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:font-medium file:bg-muted file:text-foreground hover:file:bg-muted/70"
              />
            </div>

            {error && (
              <div className="text-xs text-red-700 dark:text-red-400 bg-red-500/5 border border-red-500/20 rounded px-3 py-2">
                {error}
              </div>
            )}

            {preview && (
              <div className="space-y-2">
                <div className="flex gap-3 text-xs">
                  <span className="text-emerald-700 dark:text-emerald-400">{preview.valid_count} valid</span>
                  {preview.error_count > 0 && (
                    <span className="text-red-700 dark:text-red-400">{preview.error_count} errors</span>
                  )}
                </div>
                <div className="max-h-48 overflow-auto rounded border border-border">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-2 py-1 text-left text-muted-foreground">Email</th>
                        <th className="px-2 py-1 text-left text-muted-foreground">Name</th>
                        <th className="px-2 py-1 text-left text-muted-foreground">Workspace</th>
                        <th className="px-2 py-1 text-left text-muted-foreground">Role</th>
                        <th className="px-2 py-1 text-left text-muted-foreground">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {preview.rows.map((row, i) => (
                        <tr key={i} className={row.error ? "bg-red-500/5" : ""}>
                          <td className="px-2 py-1 text-foreground font-mono">{row.email}</td>
                          <td className="px-2 py-1 text-muted-foreground">{row.name}</td>
                          <td className="px-2 py-1 text-muted-foreground font-mono">{row.workspace_slug}</td>
                          <td className="px-2 py-1 text-muted-foreground">{row.role}</td>
                          <td className="px-2 py-1">
                            {row.error ? (
                              <span className="text-red-700 dark:text-red-400">{row.error}</span>
                            ) : (
                              <span className="text-emerald-700 dark:text-emerald-400">OK</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={handleClose}>
                Cancel
              </Button>
              {!preview ? (
                <Button size="sm" onClick={handlePreview} disabled={!file || loading}>
                  {loading ? "Parsing…" : "Preview"}
                </Button>
              ) : (
                <Button size="sm" onClick={handleExecute} disabled={loading || preview.valid_count === 0}>
                  {loading ? "Importing…" : `Import ${preview.valid_count} rows`}
                </Button>
              )}
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <div className="text-sm text-foreground">Import complete</div>
            <div className="flex gap-4 text-xs">
              <span className="text-emerald-700 dark:text-emerald-400">{result.users_created} users created</span>
              <span className="text-blue-700 dark:text-blue-400">{result.memberships_added} memberships added</span>
            </div>
            {result.errors.length > 0 && (
              <div className="space-y-1">
                {result.errors.map((e, i) => (
                  <div key={i} className="text-xs text-red-700 dark:text-red-400">
                    {e}
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end pt-2">
              <Button size="sm" onClick={handleClose}>
                Done
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
