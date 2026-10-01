import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

/** Envoltorio mínimo de ECharts: inicia, actualiza la opción, se adapta al tamaño y libera. */
export function Grafico({ opcion, alto = 280, etiqueta }: { opcion: EChartsCoreOption; alto?: number; etiqueta: string }) {
  const contenedor = useRef<HTMLDivElement>(null);
  const instancia = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const div = contenedor.current;
    if (!div) return;
    const oscuro = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
    const grafico = echarts.init(div, oscuro ? "dark" : undefined, { renderer: "canvas" });
    instancia.current = grafico;
    const observador = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => grafico.resize()) : null;
    observador?.observe(div);
    return () => {
      observador?.disconnect();
      grafico.dispose();
      instancia.current = null;
    };
  }, []);

  useEffect(() => {
    instancia.current?.setOption({ backgroundColor: "transparent", ...opcion }, true);
  }, [opcion]);

  return <div ref={contenedor} role="img" aria-label={etiqueta} style={{ height: alto, width: "100%" }} />;
}
