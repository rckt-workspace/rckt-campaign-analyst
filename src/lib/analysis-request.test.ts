import { test } from "node:test";
import assert from "node:assert/strict";
import { EMPTY_FORM, normalizeCampaign } from "./campaign-metrics";
import { inputKey, linkFor, newRequest, withFreshKey, type SavedLink } from "./analysis-request";

const TEXT = "Campaña: Retargeting Otoño\nInversión: 1000\nClics: 100";
const data = () => normalizeCampaign("paste", TEXT, EMPTY_FORM);
const savedFor = (
  input: ReturnType<typeof data>,
  campaignId = "c-1",
  metricEntryId = "e-1",
): SavedLink => ({
  key: inputKey(input),
  campaignId,
  metricEntryId,
});
const keys = (() => {
  let n = 0;
  return () => `key-${++n}`;
})();

test("sin campaña guardada, la solicitud no lleva vínculo", () => {
  const request = newRequest(data(), null, keys);
  assert.equal(request.campaignId, undefined);
  assert.equal(request.metricEntryId, undefined);
});

test("con la misma entrada guardada, el vínculo viaja", () => {
  const input = data();
  const request = newRequest(input, savedFor(input), keys);
  assert.equal(request.campaignId, "c-1");
  assert.equal(request.metricEntryId, "e-1");
});

test("con una entrada distinta, el vínculo no se envía", () => {
  const saved = savedFor(data());
  const changed = normalizeCampaign("paste", TEXT.replace("1000", "2000"), EMPTY_FORM);
  assert.deepEqual(linkFor(changed, saved), {});
});

test("timeout, guardado posterior y reintento: mismo cuerpo, misma clave, sin vínculo nuevo", () => {
  const input = data();
  const first = newRequest(input, null, keys); // el navegador agota el tiempo de espera
  const bodyBefore = JSON.stringify(first);

  // La campaña se guarda después del timeout. Ese guardado no debe cambiar la solicitud ya enviada.
  const saved = savedFor(input);
  const retry = first;

  assert.equal(JSON.stringify(retry), bodyBefore);
  assert.equal(retry.idempotencyKey, first.idempotencyKey);
  assert.equal(
    retry.campaignId,
    undefined,
    "the retry must not gain a link the first request did not have",
  );
  assert.notEqual(
    linkFor(input, saved).campaignId,
    undefined,
    "a new request for the same input would link it",
  );
});

test("el reintento conserva el vínculo original aunque se guarde otra campaña después", () => {
  const input = data();
  const first = newRequest(input, savedFor(input, "c-1", "e-1"), keys);
  const laterSave = savedFor(input, "c-2", "e-2");
  assert.equal(laterSave.campaignId, "c-2");
  assert.equal(first.campaignId, "c-1", "the retry keeps the link it was first sent with");
});

test("tras un fallo guardado por el servidor, el reintento usa una clave nueva y el mismo cuerpo", () => {
  const input = data();
  const failed = newRequest(input, savedFor(input), keys);
  const next = withFreshKey(failed, keys);
  assert.notEqual(next.idempotencyKey, failed.idempotencyKey);
  assert.equal(next.campaignId, failed.campaignId);
  assert.deepEqual(next.data, failed.data);
});

test("la huella de entrada es estable para el mismo texto", () => {
  assert.equal(inputKey(data()), inputKey(data()));
});

test("con contexto adicional no hay vínculo, aunque coincida con lo guardado", () => {
  const withContext = normalizeCampaign("form", "", {
    ...EMPTY_FORM,
    investment: "1000",
    clicks: "100",
    additionalContext: "Contexto del cliente",
  });
  assert.deepEqual(linkFor(withContext, savedFor(withContext)), {});
});
