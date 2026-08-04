/**
 * The multi-model story, told with what is actually running in this workspace.
 *
 * This is a demo surface as much as a status page. The claim it makes — that a
 * question can be routed to whichever vendor suits it, with one API, one
 * permission model and one bill — is only worth making if the page proves it
 * live rather than reciting a slide. So every number here is derived from
 * `/api/ops/models`, which resolves the roster the same way the agent does: a
 * role whose preferred model is not served in this workspace is shown falling
 * back, not quietly rendered as if it were running.
 */
import { useModelRoster } from '../lib/useModelRoster';
import { roleLabel, rosterStats, vendorBreakdown } from '../lib/modelRosterView';
import DatabricksIcon from './DatabricksIcon';

/** Vendor colours, so the same vendor reads the same everywhere on the page. */
const VENDOR_STYLES: Record<string, string> = {
  Anthropic: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
  OpenAI: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  'OpenAI (open weights)': 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300',
  Google: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  Meta: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300',
  Alibaba: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
  'Zhipu AI': 'bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300',
  'Mistral AI': 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  Databricks: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
};

function vendorStyle(vendor: string): string {
  return VENDOR_STYLES[vendor] ?? 'bg-muted text-muted-foreground';
}

/**
 * A vendor badge carrying the Databricks mark.
 *
 * The mark is doing real work rather than decorating: a reader scanning six
 * vendor names will otherwise assume six integrations, which is precisely the
 * cost this architecture avoids. Saying "served on Databricks" in text beside
 * every badge would repeat it a dozen times down the page; the mark says it once
 * per badge and costs no words. `title` spells it out for anyone who hovers.
 */
function VendorPill({ vendor }: { vendor: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${vendorStyle(vendor)}`}
      title={`${vendor} — served on Databricks Foundation Model APIs`}
    >
      <DatabricksIcon className="h-3 w-3 shrink-0 opacity-70" />
      {vendor}
    </span>
  );
}

function Stat({ value, label, hint }: { value: string | number; label: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4 text-center">
      <div className="text-2xl font-bold text-foreground">{value}</div>
      <div className="mt-0.5 text-xs font-medium text-foreground">{label}</div>
      {hint && <div className="mt-1 text-[11px] leading-snug text-muted-foreground">{hint}</div>}
    </div>
  );
}

function SectionHeading({ children, sub }: { children: string; sub?: string }) {
  return (
    <div className="mb-3">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {children}
      </h3>
      {sub && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{sub}</p>}
    </div>
  );
}

/** Findings that shaped the roster. Real constraints, kept short. */
const ENGINEERING_NOTES: { title: string; body: string; status: 'fixed' | 'constraint' }[] = [
  {
    title: 'Reasoning models leaked their scratchpad',
    status: 'fixed',
    body:
      'Some models answer with a list of typed blocks — private reasoning first, answer second. The Databricks LangChain adapter serialises that to a JSON string, so the usual "is it text?" check passes and the raw JSON reaches the user. Every read now goes through a normaliser that keeps the answer and drops the reasoning.',
  },
  {
    title: 'Gemini could not finish a tool loop',
    status: 'fixed',
    body:
      'Gemini attaches a signature to each tool call and needs it back on the next turn. The adapter rebuilds tool calls from a fixed set of fields and dropped it, so turn two was rejected — even though the raw API round-trips it fine. A shim carries the field across, and Gemini now completes multi-tool runs.',
  },
  {
    title: 'The newest model is not always the usable one',
    status: 'constraint',
    body:
      'GPT-5.5 and the 5.6 family refuse function tools on this API unless reasoning is switched off, which throws away the reason to pick them. Every specialist here is a tool loop, so GPT-5.4 is the strongest OpenAI model that actually fits. No abstraction can paper over a capability a vendor withholds.',
  },
];

export default function MultiAITab() {
  const { roster, error } = useModelRoster();
  const stats = rosterStats(roster);
  const vendors = vendorBreakdown(roster);

  if (error) {
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-700 dark:text-amber-400">
        Could not read the model roster: {error}
      </div>
    );
  }

  if (!roster) {
    return <div className="p-8 text-center text-sm text-muted-foreground">Resolving models…</div>;
  }

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="text-xl font-bold text-foreground">One platform, every model</h2>
        <p className="mx-auto mt-1 max-w-3xl text-sm leading-relaxed text-muted-foreground">
          A line manager asking “why did the filler stop?” and one asking “what should we prioritise
          next quarter?” are not the same workload, and they are not answered by the same model
          here. Each role runs on the model that suits its work — served through Databricks
          Foundation Model APIs, so every vendor sits behind one API, one set of Unity Catalog
          permissions and one bill. Changing which vendor answers a question is a config line.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat value={stats.vendorCount} label="Model vendors" hint="answering here right now" />
        <Stat value={stats.roleCount} label="Agent roles" hint="each independently routed" />
        <Stat value="1" label="API & governance plane" hint="no per-vendor integration" />
        <Stat
          value={stats.fullRoster ? 'Full' : `${stats.substituted} fallback`}
          label="Roster coverage"
          hint={
            stats.fullRoster
              ? 'every role on its preferred model'
              : 'preferred model not served here'
          }
        />
      </div>

      {!stats.multiVendor && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-400">
          Multi-vendor routing is switched off in this deployment
          (<code className="font-mono">MODEL_MULTI_VENDOR=0</code>), so every role is running on the
          two fallback tiers.
        </div>
      )}

      {stats.substituted > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
          <strong>
            {stats.substituted} role{stats.substituted === 1 ? '' : 's'} fell back to a tier
            endpoint.
          </strong>{' '}
          The preferred model is not served in this workspace, so that role runs on the default tier
          instead. Nothing is broken — but it is one fewer vendor than the roster intends. Enable the
          model under Serving → Endpoints, or pick a different one via{' '}
          <code className="font-mono">model_roles</code> in project-settings.
        </div>
      )}

      <section>
        <SectionHeading sub="Who answers what, grouped by the vendor actually serving the request.">
          Vendors on the line
        </SectionHeading>
        {/* Stated again here, next to the badges themselves. The intro makes the
            same point, but a reader scanning vendor names rarely reads up. */}
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2">
          <DatabricksIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
          <p className="text-xs leading-relaxed text-muted-foreground">
            Every vendor below is{' '}
            <strong className="text-foreground">served on Databricks Foundation Model APIs</strong>{' '}
            — no per-vendor contract, SDK or key. The{' '}
            <DatabricksIcon className="inline h-3 w-3 align-baseline" /> on each badge marks
            that. One API, one set of Unity Catalog permissions, one bill.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {vendors.map((group) => (
            <div key={group.vendor} className="rounded-lg border bg-card p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <VendorPill vendor={group.vendor} />
                <span className="text-[11px] text-muted-foreground">
                  {group.roles.length} role{group.roles.length === 1 ? '' : 's'}
                </span>
              </div>
              <div className="mb-2 font-mono text-[11px] text-muted-foreground">
                {group.models.join(' · ')}
              </div>
              <ul className="space-y-0.5">
                {group.roles.map((r) => (
                  <li key={r.role} className="text-xs text-foreground">
                    {roleLabel(r.role)}
                    {r.substituted && (
                      <span className="ml-1 text-[11px] text-amber-600 dark:text-amber-500">
                        (fallback)
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section>
        <SectionHeading sub="Each pick was made for the shape of that role's work, then checked against the live endpoints.">
          Why each model
        </SectionHeading>
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-left text-xs">
            <thead className="bg-muted/50 text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-semibold">Role</th>
                <th className="px-3 py-2 font-semibold">Model</th>
                <th className="px-3 py-2 font-semibold">Vendor</th>
                <th className="px-3 py-2 font-semibold">Reasoning behind the choice</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {roster.roles.map((r) => (
                <tr key={r.role} className="align-top">
                  <td className="whitespace-nowrap px-3 py-2 font-medium text-foreground">
                    {roleLabel(r.role)}
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">
                    {r.display}
                    {r.substituted && (
                      <div className="mt-0.5 text-amber-600 dark:text-amber-500">
                        fallback for {r.preferred_display}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <VendorPill vendor={r.vendor} />
                  </td>
                  <td className="px-3 py-2 leading-relaxed text-muted-foreground">{r.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <SectionHeading sub="Resolved once at startup, per role, in this order.">
          How a model gets picked
        </SectionHeading>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {[
            {
              n: 1,
              title: 'An explicit override',
              body: 'model_roles in project-settings, or a MODEL_<ROLE> variable. Used verbatim, never second-guessed.',
            },
            {
              n: 2,
              title: 'The roster pick',
              body: 'The model chosen for that role above — used when this workspace actually serves it.',
            },
            {
              n: 3,
              title: 'The tier fallback',
              body: 'Otherwise the role degrades to its default tier and says so here, rather than failing the deploy.',
            },
          ].map((step) => (
            <div key={step.n} className="rounded-lg border bg-card p-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">
                  {step.n}
                </span>
                <span className="text-xs font-semibold text-foreground">{step.title}</span>
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground">{step.body}</p>
            </div>
          ))}
        </div>
        <div className="mt-3 rounded-lg border bg-muted/30 p-3">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Change it
          </div>
          <pre className="overflow-x-auto text-[11px] leading-relaxed text-foreground">
            <code>{`# config/project-settings.yaml
model_roles:
  downtime_analyst: 'databricks-claude-sonnet-5'
  oee_coach: 'databricks-gpt-5-4'

# see what this workspace serves, per role
python3 scripts/preflight.py dev --models`}</code>
          </pre>
        </div>
      </section>

      <section>
        {/* The count comes from the roster for the same reason the Stat above
            does: in a workspace where two roles fell back, a hardcoded "six"
            sits directly below a stat reading four. */}
        <SectionHeading
          sub={`Running one agent across ${stats.vendorCount} vendors surfaces things a single-vendor build never hits. These were found by testing against the live endpoints, not by reading docs.`}
        >
          What it took
        </SectionHeading>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {ENGINEERING_NOTES.map((note) => (
            <div key={note.title} className="rounded-lg border bg-card p-3">
              <div className="mb-1.5 flex items-start justify-between gap-2">
                <span className="text-xs font-semibold text-foreground">{note.title}</span>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    note.status === 'fixed'
                      ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300'
                      : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {note.status === 'fixed' ? 'handled' : 'vendor limit'}
                </span>
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground">{note.body}</p>
            </div>
          ))}
        </div>
      </section>

      <p className="text-center text-[11px] text-muted-foreground">
        Every answer in the chat carries the vendor that produced it — expand “How this decision was
        made” on any reply to see which model ran.
      </p>
    </div>
  );
}
