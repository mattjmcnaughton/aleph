import type { Meta, StoryObj } from "@storybook/react-vite";
import tailwindConfig from "../../tailwind.config";

// The design tokens as they are today (`tailwind.config.ts`): a reference for
// anyone changing them, and the first place a redesign should show its palette.

const colors = tailwindConfig.theme.extend.colors as Record<
  string,
  string | Record<string, string>
>;

function Swatch({ name, value }: { name: string; value: string }) {
  return (
    <div className="w-28">
      <div className="h-14 rounded-md border border-divider" style={{ background: value }} />
      <p className="mt-1 text-xs tabular-nums text-porcelain">{name}</p>
      <p className="text-xs tabular-nums text-slate">{value}</p>
    </div>
  );
}

function Tokens() {
  return (
    <main className="min-h-screen space-y-10 bg-night p-6 text-porcelain">
      <section>
        <h2 className="kicker">Colour</h2>
        <div className="mt-4 flex flex-wrap gap-4">
          {Object.entries(colors).flatMap(([name, value]) =>
            typeof value === "string"
              ? [<Swatch key={name} name={name} value={value} />]
              : Object.entries(value).map(([shade, hex]) => (
                  <Swatch
                    key={`${name}-${shade}`}
                    name={shade === "DEFAULT" ? name : `${name}-${shade}`}
                    value={hex}
                  />
                )),
          )}
        </div>
      </section>
      <section>
        <h2 className="kicker">Type</h2>
        <div className="mt-4 max-w-[40rem] space-y-4">
          <div>
            <p className="kicker">Page title: Literata 500, .page-title</p>
            <p className="page-title mt-1">Rust ownership &amp; lifetimes</p>
          </div>
          <div>
            <p className="kicker">Section title: Literata 500, 20px</p>
            <p className="mt-1 font-serif text-xl font-medium">Lifetimes in practice</p>
          </div>
          <div>
            <p className="kicker">Reading: Literata, 17px / 1.7, .reading</p>
            <p className="reading mt-1">
              A <strong>lifetime</strong> is the compiler's name for{" "}
              <em>how long a reference is guaranteed to point at valid data</em>.
            </p>
          </div>
          <div>
            <p className="kicker">UI: Instrument Sans, 15px</p>
            <p className="mt-1 text-[15px]">Pick up where you left off</p>
            <p className="text-sm text-mist">Rust ownership &amp; lifetimes · 4 of 11 complete</p>
          </div>
          <div>
            <p className="kicker">Code: JetBrains Mono, code only</p>
            <p className="mt-1 font-mono text-sm">
              fn longest&lt;'a&gt;(x: &amp;'a str) -&gt; &amp;'a str
            </p>
          </div>
        </div>
      </section>
      <section>
        <h2 className="kicker">Elevation</h2>
        <div className="mt-4 flex flex-wrap gap-6">
          {["shadow-sm", "shadow-md", "shadow-lg", "shadow-glow", "shadow-glow-iris"].map((s) => (
            <div key={s} className={`h-20 w-32 rounded-lg bg-surface p-3 text-xs ${s}`}>
              {s}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

const meta = { title: "Foundations/Tokens", component: Tokens } satisfies Meta<typeof Tokens>;
export default meta;
export const Current: StoryObj<typeof meta> = {};
