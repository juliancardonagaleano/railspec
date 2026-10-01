// Página de prueba independiente del chat, para usar hasta que exista el
// shell de la consola (router + autenticación). No persiste el token.

import { useId, useState } from "react";
import type { FormEvent } from "react";
import { ChatContexto } from "./ChatContexto";
import "./chat.css";

interface Config {
  apiBase: string;
  token: string;
  org: string;
  workspace: string;
  repositorios?: string[];
  conversacionId?: string;
}

export function PaginaPruebaChat() {
  const ids = {
    apiBase: useId(),
    token: useId(),
    org: useId(),
    workspace: useId(),
    repos: useId(),
    conversacion: useId(),
  };
  const [apiBase, setApiBase] = useState("http://localhost:8000");
  const [token, setToken] = useState("");
  const [org, setOrg] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [repos, setRepos] = useState("");
  const [conversacionId, setConversacionId] = useState("");
  const [config, setConfig] = useState<Config | null>(null);
  const [version, setVersion] = useState(0);

  function abrir(e: FormEvent) {
    e.preventDefault();
    setConfig({
      apiBase: apiBase.trim(),
      token: token.trim(),
      org: org.trim(),
      workspace: workspace.trim(),
      // Vacío = el propio componente pide los repositorios.
      repositorios: repos.trim() ? repos.split(",").map((r) => r.trim()).filter((r) => r !== "") : undefined,
      conversacionId: conversacionId.trim() || undefined,
    });
    setVersion((v) => v + 1);
  }

  return (
    <div>
      <div className="rs-chat">
        <h2>Prueba del chat de contexto</h2>
        <form className="rs-chat-prueba-config" onSubmit={abrir}>
          <label htmlFor={ids.apiBase}>API base</label>
          <input id={ids.apiBase} type="url" required value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
          <label htmlFor={ids.token}>Token</label>
          <input
            id={ids.token}
            type="password"
            required
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <label htmlFor={ids.org}>Org</label>
          <input id={ids.org} type="text" required value={org} onChange={(e) => setOrg(e.target.value)} />
          <label htmlFor={ids.workspace}>Workspace</label>
          <input
            id={ids.workspace}
            type="text"
            required
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
          />
          <label htmlFor={ids.repos}>Repositorios (coma; vacío = los pide el chat)</label>
          <input
            id={ids.repos}
            type="text"
            placeholder="acme/api, acme/web"
            value={repos}
            onChange={(e) => setRepos(e.target.value)}
          />
          <label htmlFor={ids.conversacion}>Conversación existente (opcional)</label>
          <input
            id={ids.conversacion}
            type="text"
            value={conversacionId}
            onChange={(e) => setConversacionId(e.target.value)}
          />
          <span />
          <button type="submit">{config ? "Reabrir" : "Abrir chat"}</button>
        </form>
      </div>
      {config && (
        <ChatContexto
          key={version}
          apiBase={config.apiBase}
          token={config.token}
          org={config.org}
          workspace={config.workspace}
          repositorios={config.repositorios}
          conversacionId={config.conversacionId}
          alCrearConversacion={(id) => setConversacionId(id)}
        />
      )}
    </div>
  );
}

export default PaginaPruebaChat;
