import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { BASE_SPA, textoError } from "../../api/cliente";
import { auth, claves } from "../../api/endpoints";
import { Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Campo, Input } from "../../componentes/ui/input";

/** Solo rutas internas de la SPA: evita redirecciones abiertas. */
export function volverSeguro(volver: string | undefined): string {
  if (!volver || !volver.startsWith("/") || volver.startsWith("//")) return "/";
  return volver;
}

export function Login() {
  const busqueda = useSearch({ strict: false }) as { volver?: string };
  const volver = volverSeguro(busqueda.volver);
  const config = useQuery({ queryKey: claves.configAuth, queryFn: auth.config, retry: false });
  const [token, setToken] = useState("");

  const entrarDesarrollo = useMutation({
    mutationFn: (t: string) => auth.desarrollo(t),
    onSuccess: () => window.location.assign(`${BASE_SPA}${volver}`),
  });

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (token.trim()) entrarDesarrollo.mutate(token.trim());
  };

  return (
    <main className="flex min-h-full items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">Railspec · consola</CardTitle>
          <CardDescription>Entra para ver tus unidades, el grafo de código y la configuración.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {config.isPending ? <Cargando texto="Consultando métodos de acceso…" /> : null}
          {config.isError ? <ErrorVista error={config.error} reintentar={() => void config.refetch()} /> : null}
          {config.data && !config.data.github && !config.data.desarrollo ? (
            <Vacio titulo="No hay métodos de acceso configurados">Pide al administrador que configure GitHub.</Vacio>
          ) : null}
          {config.data?.github ? (
            <Button onClick={() => window.location.assign(auth.urlGithub(volver))}>Entrar con GitHub</Button>
          ) : null}
          {config.data?.desarrollo ? (
            <form onSubmit={enviar} className="flex flex-col gap-2 border-t border-borde pt-4">
              <Campo etiqueta="Token de desarrollo" htmlFor="token-desarrollo" ayuda="Solo en servidores de desarrollo.">
                <Input
                  id="token-desarrollo"
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </Campo>
              <Button type="submit" variante="secundario" disabled={!token.trim() || entrarDesarrollo.isPending}>
                {entrarDesarrollo.isPending ? "Entrando…" : "Entrar con token"}
              </Button>
              {entrarDesarrollo.isError ? (
                <p role="alert" className="text-sm text-peligro">
                  {textoError(entrarDesarrollo.error)}
                </p>
              ) : null}
            </form>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}
