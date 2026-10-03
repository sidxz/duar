import { useRef, useState } from "react";
import worldUntyped from "@svg-maps/world";
import type { CountryCount } from "../types/api";

// The package's d.ts points at an uninstalled "svg-maps__common" and decays to
// `any` — pin the actual shape here.
const world = worldUntyped as {
  viewBox: string;
  locations: { path: string; id: string; name: string }[];
};

interface Hover {
  name: string;
  count: number;
  x: number;
  y: number;
}

/** Choropleth of sign-in counts by country. Sequential blue ramp via the
 *  --map-* tokens (dark mode flips the anchor); countries with no data recede
 *  to --map-zero. Values are also listed beside the map (relief channel), so
 *  the fill never carries the numbers alone. */
export function WorldMap({ countries }: { countries: CountryCount[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  const byCode = new Map(countries.map((c) => [c.code.toLowerCase(), c]));
  const max = Math.max(1, ...countries.map((c) => c.count));

  const fill = (count: number | undefined) => {
    if (!count) return "var(--map-zero)";
    const idx = Math.min(5, Math.max(1, Math.ceil((count / max) * 5)));
    return `var(--map-${idx})`;
  };

  return (
    <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
      <svg viewBox={world.viewBox} className="w-full h-auto" role="img" aria-label="Sign-ins by country">
        {world.locations.map((loc) => {
          const c = byCode.get(loc.id);
          return (
            <path
              key={loc.id}
              d={loc.path}
              fill={fill(c?.count)}
              stroke={hover?.name === loc.name ? "var(--muted-foreground)" : "var(--card)"}
              strokeWidth={hover?.name === loc.name ? 1 : 0.5}
              onMouseMove={(e) => {
                const r = ref.current?.getBoundingClientRect();
                if (!r) return;
                setHover({
                  name: loc.name,
                  count: c?.count ?? 0,
                  x: e.clientX - r.left,
                  y: e.clientY - r.top,
                });
              }}
            />
          );
        })}
      </svg>
      {hover && (
        <div
          className="absolute pointer-events-none bg-popover text-popover-foreground border border-border rounded-md shadow-md px-2.5 py-1.5 text-xs whitespace-nowrap"
          style={{
            left: hover.x,
            top: hover.y,
            transform: `translate(${hover.x > (ref.current?.clientWidth ?? 0) / 2 ? "calc(-100% - 10px)" : "10px"}, -50%)`,
          }}
        >
          <span className="font-semibold">{hover.count.toLocaleString()}</span>{" "}
          <span className="text-muted-foreground">{hover.name}</span>
        </div>
      )}
    </div>
  );
}
