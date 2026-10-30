import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeFastapiBaseUrl } from "./fastapi-client.server";

test("a full http URL is kept, without a trailing slash", () => {
  assert.equal(normalizeFastapiBaseUrl("http://rckt-campaign-api:8000/"), "http://rckt-campaign-api:8000");
});

test("a bare host:port from Render's private network gets the http scheme", () => {
  assert.equal(normalizeFastapiBaseUrl("rckt-campaign-api:10000"), "http://rckt-campaign-api:10000");
});

test("an https URL is kept as configured", () => {
  assert.equal(normalizeFastapiBaseUrl("https://api.example.test"), "https://api.example.test");
});

test("an unset or blank value falls back to the local development address", () => {
  assert.equal(normalizeFastapiBaseUrl(undefined), "http://127.0.0.1:8000");
  assert.equal(normalizeFastapiBaseUrl("   "), "http://127.0.0.1:8000");
});
