/**
 * The Databricks mark, shared by the context tabs.
 *
 * Used on vendor pills to carry a claim the prose can only make once: every
 * model here is served through Databricks, whatever the vendor name says. A row
 * of vendor badges is what the eye lands on, and without the mark it reads as
 * six separate integrations — the opposite of the point.
 *
 * Geometry is the official mark from Simple Icons (CC0), not a hand-drawn
 * approximation. The previous version was vertically flipped: its bands peaked
 * upward, where the real mark's bands dip downward in the middle — the shape is
 * meant to read as data flowing down into a unified platform, so inverting it
 * inverts the idea. Do not redraw this by hand.
 */
export default function DatabricksIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M.95 14.184L12 20.403l9.919-5.55v2.21L12 22.662l-10.484-5.96-.565.308v.77L12 24l11.05-6.218v-4.317l-.515-.309L12 19.118l-9.867-5.653v-2.21L12 16.805l11.05-6.218V6.32l-.515-.308L12 11.974 2.647 6.681 12 1.388l7.76 4.368.668-.411v-.566L12 0 .95 6.27v.72L12 13.207l9.919-5.55v2.26L12 15.52 1.516 9.56l-.565.308Z" />
    </svg>
  );
}
