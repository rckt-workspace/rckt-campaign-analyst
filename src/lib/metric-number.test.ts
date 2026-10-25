import { test } from "node:test";
import assert from "node:assert/strict";
import { readMetricNumber, type MetricKind } from "./metric-number";

const ok = (input: string, kind: MetricKind, value: number) =>
  assert.deepEqual(readMetricNumber(input, kind), { status: "ok", value }, `${input} (${kind})`);

const invalid = (input: string, kind: MetricKind, reason: string) =>
  assert.deepEqual(
    readMetricNumber(input, kind),
    { status: "invalid", reason },
    `${input} (${kind})`,
  );

test("formatos colombianos válidos", () => {
  ok("4.850.000", "money", 4850000);
  ok("$4.850.000 COP", "money", 4850000);
  ok("1.234,56", "money", 1234.56);
  ok("2,47", "ratio", 2.47);
  ok("1,67", "ratio", 1.67);
  ok("2.10%", "percent", 2.1);
  ok("$563", "unit_cost", 563);
  ok("$12.500", "unit_cost", 12500);
  ok("410.000", "count", 410000);
  ok("8.610", "count", 8610);
});

test("formatos internacionales válidos", () => {
  ok("1,234.56", "money", 1234.56);
  ok("1234.56", "money", 1234.56);
  ok("4,850,000", "money", 4850000);
  ok("1234567.89", "money", 1234567.89);
});

test("el caso que altera cifras, 1234,567, nunca se convierte en 1234567", () => {
  const reading = readMetricNumber("1234,567", "money");
  assert.deepEqual(reading, { status: "invalid", reason: "ambiguous" });
  assert.notEqual(reading.status, "ok");
});

test("separadores ambiguos: un separador seguido de tres cifras se rechaza sin contexto", () => {
  invalid("1234,567", "money", "ambiguous");
  invalid("1,234", "money", "ambiguous");
  invalid("4.850", "money", "ambiguous");
  invalid("0,500", "ratio", "ambiguous");
  invalid("1.234", "ratio", "ambiguous");
});

test("un punto seguido de tres cifras es miles cuando es la única lectura posible", () => {
  // Conteos no admiten decimales: "8.610" solo puede ser 8610.
  ok("8.610", "count", 8610);
  ok("1.234", "count", 1234);
  // Importes con marca de pesos ($ o COP): el punto agrupa miles en Colombia.
  ok("$4.850", "money", 4850);
  ok("$9.291", "unit_cost", 9291);
  ok("$12.500", "unit_cost", 12500);
  ok("COP 1.234", "money", 1234);
  // Sin marca de pesos el mismo texto sigue siendo ambiguo para importes y ratios.
  invalid("9.291", "unit_cost", "ambiguous");
  // Una coma nunca agrupa miles de forma implícita.
  invalid("$1,234", "money", "ambiguous");
});

test("precisión excesiva: se rechaza, nunca se redondea", () => {
  invalid("1,234.567", "money", "too_many_decimals");
  invalid("12.345,678", "money", "too_many_decimals");
  invalid("0,12345", "unit_cost", "too_many_decimals");
  invalid("1,23456", "ratio", "too_many_decimals");
  invalid("2,22225", "ratio", "too_many_decimals");
  invalid("1,234567", "percent", "too_many_decimals");
});

test("decimales dentro de la precisión permitida", () => {
  ok("0,1234", "ratio", 0.1234);
  ok("0,1234", "unit_cost", 0.1234);
  ok("12.345,6789", "unit_cost", 12345.6789);
  ok("1.234,5", "money", 1234.5);
});

test("importes grandes: el máximo de cada columna se acepta y uno mayor se rechaza", () => {
  ok("999.999.999.999,99", "money", 999999999999.99);
  invalid("1.000.000.000.000", "money", "too_large");
  invalid("1000000000000", "money", "too_large");
  invalid("12345678901,2", "unit_cost", "too_large");
  invalid("123456789,1", "ratio", "too_large");
  invalid("123456,1", "percent", "too_large");
  invalid("1000000000000000", "count", "too_large");
});

test("conteos: fraccionarios se rechazan, ceros decimales no", () => {
  invalid("1,5", "count", "fractional_count");
  invalid("8.5", "count", "fractional_count");
  ok("8,0", "count", 8);
  ok("007", "count", 7);
});

test("valores negativos", () => {
  invalid("-5", "money", "negative");
  invalid("-2,5", "ratio", "negative");
  invalid("-4.850.000", "money", "negative");
});

test("valores malformados", () => {
  invalid("abc", "money", "malformed");
  invalid("4 850 000", "money", "malformed");
  invalid("1234,", "money", "malformed");
  invalid(",56", "money", "malformed");
  invalid("12.34.5", "money", "malformed");
  invalid("1,2,3.4", "money", "malformed");
  invalid("-", "money", "malformed");
  invalid("1e5", "money", "malformed");
});

test("vacío no es un error", () => {
  assert.deepEqual(readMetricNumber("", "money"), { status: "empty" });
  assert.deepEqual(readMetricNumber("   ", "count"), { status: "empty" });
});
