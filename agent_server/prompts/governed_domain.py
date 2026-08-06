"""The governed-domain fragment appended to a specialist's system prompt.

`governed_domain_prompt()` in `agent_server.tools.governed_domain_query`
renders this at agent-construction time and appends it to the specialist's
constant, so what a governed specialist actually receives is roughly 15%
text that used to exist only inside that function.

Keeping it here makes that 15% registrable like everything else. Whether it is
appended at all still depends on the deployment — a domain with no Genie space
provisioned gets an empty string, because describing a tool the model cannot
call only invites hallucinated calls — but the text itself is fixed, so the
registered version is the whole of what a provisioned deployment sends.

`{domain}` is the domain name and `{entities}` is its one-line entity prose.
"""

GOVERNED_DOMAIN_SUFFIX_TEMPLATE = (
    "\n\nYOUR GOVERNED DOMAIN SPACE (governed_{domain}_query):\n"
    "- You have a dedicated Genie space over the governed {domain} domain: "
    "{entities}.\n"
    "- Prefer it for open-ended or exploratory questions about that domain, "
    "and for governed/enterprise phrasing (equipment register, PM schedule, "
    "NCR, stock balance, plan-vs-actual).\n"
    "- Prefer sql_query for the live streaming tables, for anything "
    "time-critical, and whenever you already know the exact query — it is "
    "faster and deterministic. Genie is a round trip.\n"
    "- The space covers ONLY your domain. Do not ask it about other domains; "
    "use sql_query with fully qualified names for cross-domain questions."
)
