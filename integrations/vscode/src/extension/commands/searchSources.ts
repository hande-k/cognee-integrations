import * as vscode from "vscode";
import type { Runtime } from "../runtime";

export async function searchSourcesCommand(runtime: Runtime): Promise<void> {
  const query = await vscode.window.showInputBox({prompt: "Search your connected sources"});
  if (!query?.trim()) return;
  const sourceHint = await vscode.window.showInputBox({prompt: "Source name or description (leave empty to discover relevant sources)"});
  if (sourceHint === undefined) return;
  await vscode.window.withProgress({location: vscode.ProgressLocation.Notification,
    title: "Cognee: searching sources…", cancellable: true}, async (_progress, token) => {
    const controller = new AbortController();
    const disposable = token.onCancellationRequested(() => controller.abort());
    try {
      if (!runtime.client.searchSources) throw new Error("Source search unavailable");
      const result = await runtime.client.searchSources(query, {sourceHint: sourceHint || undefined, signal: controller.signal});
      const document = await vscode.workspace.openTextDocument({language: "json", content: JSON.stringify(result, null, 2)});
      await vscode.window.showTextDocument(document, {preview: true});
    } catch {
      void vscode.window.showErrorMessage("Cognee source search unavailable. Check server support and caller permissions.");
    } finally { disposable.dispose(); }
  });
}
