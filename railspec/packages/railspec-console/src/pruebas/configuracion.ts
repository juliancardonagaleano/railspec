import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// jsdom no implementa scrollTo (lo usa la restauración de scroll del router).
window.scrollTo = (() => undefined) as typeof window.scrollTo;
