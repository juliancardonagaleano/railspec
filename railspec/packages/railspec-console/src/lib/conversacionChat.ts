// Última conversación del chat por workspace, para retomarla al volver a la ruta.
// Solo se guarda el id (un uuid): sin el token de su autora no abre nada, y el servidor responde 404
// a quien no es la autora o cuando la conversación expiró; en ese caso la consola la olvida.

const PREFIJO = "railspec.consola.chat.";
const PATRON_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const clave = (org: string, ws: string) => `${PREFIJO}${org}/${ws}`;

function almacen(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null; // navegación privada o almacenamiento bloqueado
  }
}

export function leerUltimaConversacion(org: string, ws: string): string | undefined {
  try {
    const id = almacen()?.getItem(clave(org, ws));
    return id && PATRON_UUID.test(id) ? id : undefined;
  } catch {
    return undefined;
  }
}

export function guardarUltimaConversacion(org: string, ws: string, id: string): void {
  try {
    if (PATRON_UUID.test(id)) almacen()?.setItem(clave(org, ws), id);
  } catch {
    // sin almacenamiento: simplemente no se retoma
  }
}

export function olvidarUltimaConversacion(org: string, ws: string): void {
  try {
    almacen()?.removeItem(clave(org, ws));
  } catch {
    // nada que olvidar
  }
}

/** Al cerrar sesión: que la siguiente persona del navegador no arranque sobre la conversación de otra. */
export function olvidarTodasLasConversaciones(): void {
  try {
    const a = almacen();
    if (!a) return;
    for (const k of Object.keys(a)) if (k.startsWith(PREFIJO)) a.removeItem(k);
  } catch {
    // nada que olvidar
  }
}
