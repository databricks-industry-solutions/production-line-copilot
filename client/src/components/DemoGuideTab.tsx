/**
 * The presenter's talk track, inside the app it describes.
 *
 * This accelerator is public and gets demoed by people who did not build it,
 * and until now the only guide was a README — which means it was read before
 * the demo, if at all, and never during. A guide that lives in the product is
 * open on the second monitor while the line runs on the first.
 *
 * The copy itself lives in `lib/demoScript`, which is where the reasoning
 * about derived counts and the absent ROI figure is written down. This file is
 * only the rendering of it.
 */
import { MACHINE_LAYOUT } from '../lib/constants';
import { DEMO_STEPS, HONEST_ANSWERS, totalMinutes } from '../lib/demoScript';
import { OPTIMIZER_METHODS, SPECIALISTS } from '../lib/specialistCatalog';
import { GOVERNED_DOMAINS } from '../lib/topologyCopy';
import type { TabId } from '../lib/tabRouting';

function StatCard({ value, label, note }: { value: string; label: string; note: string }) {
  return (
    <div className="rounded-lg border bg-card p-4 text-center">
      <div className="text-2xl font-bold text-foreground">{value}</div>
      <div className="mt-0.5 text-xs font-medium text-foreground">{label}</div>
      <div className="mt-1 text-[11px] leading-snug text-muted-foreground">{note}</div>
    </div>
  );
}

function ProductChip({ name }: { name: string }) {
  return (
    <span className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      {name}
    </span>
  );
}

export default function DemoGuideTab({ onOpenTab }: { onOpenTab: (tab: TabId) => void }) {
  const minutes = totalMinutes();

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-card p-5">
        <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Demo guide</div>
        <h2 className="mt-1 text-xl font-bold text-foreground">
          You are the line manager, and the shift is going fine until it isn&rsquo;t
        </h2>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted-foreground">
          {DEMO_STEPS.length} beats, about {minutes} minutes, in the order the story works. Each one has the words to
          say, the thing to point at, and a button that takes you straight to the view being described. Read the scripts
          out loud if you like — they are written to survive it.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard value={String(MACHINE_LAYOUT.length)} label="machines" note="one bottling line, buffers between" />
        <StatCard value={String(SPECIALISTS.length)} label="specialists" note="a router picks one, not all of them" />
        <StatCard
          value={String(Object.keys(OPTIMIZER_METHODS).length)}
          label="optimizers"
          note="deterministic solvers, no model in the path"
        />
        <StatCard
          value={String(GOVERNED_DOMAINS)}
          label="governed domains"
          note="one Genie space per Unity Catalog schema"
        />
      </div>

      <ol className="space-y-3">
        {DEMO_STEPS.map((step, index) => (
          <li key={step.id} className="rounded-lg border bg-card p-4" style={{ animationDelay: `${index * 40}ms` }}>
            <div className="flex items-start gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-bold text-foreground">
                {index + 1}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground">{step.title}</h3>
                  <span className="font-mono text-[11px] text-muted-foreground">~{step.seconds}s</span>
                </div>

                <blockquote className="mt-2 border-l-2 border-border pl-3 text-sm leading-relaxed text-muted-foreground">
                  {step.script}
                </blockquote>

                <div className="mt-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Point at
                </div>
                <ul className="mt-1 space-y-0.5">
                  {step.pointAt.map((item) => (
                    <li key={item} className="text-xs leading-relaxed text-muted-foreground">
                      — {item}
                    </li>
                  ))}
                </ul>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  {step.products.map((name) => (
                    <ProductChip key={name} name={name} />
                  ))}
                </div>

                <button
                  type="button"
                  onClick={() => onOpenTab(step.tab)}
                  className="mt-3 rounded border border-border bg-muted px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-border"
                >
                  Open {step.tabLabel} →
                </button>
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="rounded-lg border bg-card p-5">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">When they push back</h3>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
          Three questions come up almost every time. None of them has a defensive answer, which is the point.
        </p>
        <div className="mt-3 space-y-3">
          {HONEST_ANSWERS.map((item) => (
            <div key={item.question}>
              <div className="text-xs font-semibold text-foreground">{item.question}</div>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{item.answer}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
