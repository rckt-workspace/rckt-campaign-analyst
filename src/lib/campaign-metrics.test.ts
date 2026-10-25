import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EMPTY_FORM,
  EXAMPLE_FORM,
  EXAMPLE_RAW,
  countFilledFields,
  describeIssue,
  normalizeCampaign,
} from "./campaign-metrics";

const pasted = (text: string) => normalizeCampaign("paste", text, EMPTY_FORM);
const typed = (overrides: Partial<typeof EMPTY_FORM>) =>
  normalizeCampaign("form", "", { ...EMPTY_FORM, ...overrides });

test("el ejemplo de texto pegado se normaliza sin incidencias y con los valores esperados", () => {
  const data = pasted(EXAMPLE_RAW);
  assert.deepEqual(data.issues, []);
  assert.equal(data.investment, 4850000);
  assert.equal(data.impressions, 410000);
  assert.equal(data.reach, 245000);
  assert.equal(data.frequency, 1.67);
  assert.equal(data.clicks, 8610);
  assert.equal(data.ctr, 2.1);
  assert.equal(data.cpc, 563);
  assert.equal(data.leads, 522);
  assert.equal(data.cpl, 9291);
  assert.equal(data.conversions, 94);
  assert.equal(data.revenue, 11960000);
  assert.equal(data.roas, 2.47);
  assert.deepEqual(data.previous, { ctr: 1.65, cpc: 720, cpl: 12500, roas: 1.95 });
});

test("el ejemplo del formulario se normaliza sin incidencias y con los mismos valores", () => {
  const data = normalizeCampaign("form", "", EXAMPLE_FORM);
  assert.deepEqual(data.issues, []);
  assert.equal(data.investment, 4850000);
  assert.equal(data.frequency, 1.67);
  assert.equal(data.ctr, 2.1);
  assert.equal(data.cpl, 9291);
  assert.equal(data.revenue, 11960000);
});

test("1234,567 en texto pegado no se convierte en 1234567: queda como incidencia con su texto original", () => {
  const data = pasted("Campaña: X\nInversión: 1234,567");
  assert.equal(data.investment, null);
  assert.equal(data.issues.length, 1);
  const [issue] = data.issues;
  assert.equal(issue?.field, "investment");
  assert.equal(issue?.raw, "1234,567");
  assert.equal(issue?.reason, "ambiguous");
  assert.match(describeIssue(issue!), /Inversión: «1234,567»/);
});

test("1234,567 en el formulario tampoco se convierte: bloquea con incidencia", () => {
  const data = typed({ investment: "1234,567" });
  assert.equal(data.investment, null);
  assert.equal(data.issues[0]?.field, "investment");
  assert.equal(data.issues[0]?.reason, "ambiguous");
});

test("precisión excesiva en importes se rechaza, no se redondea", () => {
  const data = typed({ investment: "1.234,567", revenue: "10.000,001" });
  assert.equal(data.investment, null);
  assert.equal(data.revenue, null);
  assert.deepEqual(
    data.issues.map((issue) => [issue.field, issue.reason]),
    [
      ["investment", "too_many_decimals"],
      ["revenue", "too_many_decimals"],
    ],
  );
});

test("periodo anterior: un valor ambiguo se señala con su propia etiqueta", () => {
  const data = pasted(
    "Periodo actual: 1 al 15 de agosto\nPeriodo anterior:\nCTR: 1,234\nROAS: 1,95",
  );
  assert.equal(data.previous.ctr, undefined);
  assert.equal(data.previous.roas, 1.95);
  assert.equal(data.issues.length, 1);
  assert.equal(data.issues[0]?.field, "previous.ctr");
  assert.equal(data.issues[0]?.label, "CTR (periodo anterior)");
  assert.equal(data.issues[0]?.raw, "1,234");
});

test("conteos fraccionarios, negativos y malformados quedan como incidencias", () => {
  const data = typed({ clicks: "8,5", leads: "-3", reach: "abc" });
  // Issues follow the order of the form fields: reach, clicks, leads.
  assert.deepEqual(
    data.issues.map((issue) => [issue.field, issue.reason]),
    [
      ["reach", "malformed"],
      ["clicks", "fractional_count"],
      ["leads", "negative"],
    ],
  );
  assert.equal(data.clicks, null);
  assert.equal(data.leads, null);
  assert.equal(data.reach, null);
});

test("valores vacíos no generan incidencias", () => {
  const data = typed({ investment: "   ", clicks: "" });
  assert.deepEqual(data.issues, []);
  assert.equal(data.investment, null);
});

test("las incidencias bloquean; un número seguro sigue contando como campo lleno", () => {
  const blocked = pasted("Campaña: X\nInversión: 1234,567\nClics: 8.610");
  const clean = pasted("Campaña: X\nInversión: 4.850.000\nClics: 8.610");
  assert.equal(blocked.issues.length, 1);
  assert.equal(clean.issues.length, 0);
  assert.equal(countFilledFields(clean), 3);
  assert.equal(countFilledFields(blocked), 2);
});
