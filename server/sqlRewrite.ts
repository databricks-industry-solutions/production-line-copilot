export function rewriteQualifiedSchema(
  sqlText: string,
  opts?: {
    ucCatalog?: string;
    ucSchema?: string;
    defaultCatalog?: string;
    defaultSchema?: string;
  }
): string {
  const rawCatalog = opts?.ucCatalog ?? process.env.UC_CATALOG ?? '';
  const rawSchema = opts?.ucSchema ?? process.env.UC_SCHEMA ?? '';

  const looksLikePlaceholder = (v: string) => v.includes('${') || v.includes('$') || v.includes('{var.');

  const catalog = rawCatalog && !looksLikePlaceholder(rawCatalog) ? rawCatalog : (opts?.defaultCatalog ?? 'main');
  const schema =
    rawSchema && !looksLikePlaceholder(rawSchema) ? rawSchema : (opts?.defaultSchema ?? 'line_manager_ai_kit_preview');

  const targetPrefix = `${catalog}.${schema}.`;

  // The .sql files under config/queries/ are authored against a fixed *template*
  // FQN prefix (`main.line_manager_ai_kit.`) and rewritten here to the deploy's
  // real `catalog.schema.` (from UC_CATALOG/UC_SCHEMA). `line_manager_ai_kit`
  // here is ONLY an authoring token — it is NOT the live runtime schema
  // (that's `line_manager_streaming`, see databricks.yml). Override the token
  // with SQL_TEMPLATE_CATALOG / SQL_TEMPLATE_SCHEMA if you re-author the queries.
  const templateCatalog = process.env.SQL_TEMPLATE_CATALOG ?? 'main';
  const templateSchema = process.env.SQL_TEMPLATE_SCHEMA ?? 'line_manager_ai_kit';
  const templatePrefix = `${templateCatalog}.${templateSchema}.`;

  return sqlText
    .split(templatePrefix)
    .join(targetPrefix)
    .split('main.line_manager_ai_kit.')
    .join(targetPrefix)
    .split('${var.catalog}.${var.schema}.')
    .join(targetPrefix)
    .split('${catalog}.${schema}.')
    .join(targetPrefix);
}
