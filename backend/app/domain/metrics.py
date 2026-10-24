"""Deterministic campaign calculations. The application calculates; the LLM interprets.

Port of src/lib/metrics-engine.ts to put authoritative metrics on the Python side.
React retains its client-side calculation for immediate visual warnings.
"""

import math
import re
from dataclasses import dataclass

from app.domain.schemas import CampaignInput


@dataclass(frozen=True)
class MetricsReport:
    calculated: dict[str, float | None]
    comparisons: list[dict]
    warnings: list[dict]


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def js_round(value: float, decimals: int = 2) -> float:
    """Mimic Math.round of the original TypeScript implementation."""
    factor = 10 ** decimals
    return math.floor(value * factor + 0.5) / factor


def rounded(value: float | None, decimals: int = 2) -> float | None:
    return js_round(value, decimals) if value is not None else None


def calculate_metrics(data: CampaignInput) -> dict[str, float | None]:
    ctr = safe_divide(data.clicks, data.impressions)
    conversion = safe_divide(data.conversions, data.clicks)
    return {
        "ctr": rounded(ctr * 100 if ctr is not None else None, 4),
        "cpc": rounded(safe_divide(data.investment, data.clicks)),
        "cpl": rounded(safe_divide(data.investment, data.leads)),
        "cpa": rounded(safe_divide(data.investment, data.conversions)),
        "roas": rounded(safe_divide(data.revenue, data.investment), 4),
        "conversionRate": rounded(conversion * 100 if conversion is not None else None, 4),
        "frequency": rounded(safe_divide(data.impressions, data.reach), 4),
    }


def within_tolerance(reported: float, calculated: float) -> bool:
    diff = abs(reported - calculated)
    scale = max(abs(reported), abs(calculated))
    return scale == 0 or diff <= 0.01 or diff / scale <= 0.03


LABELS = {
    "investment": "Inversión", "impressions": "Impresiones", "reach": "Alcance",
    "frequency": "Frecuencia", "clicks": "Clics", "ctr": "CTR", "cpc": "CPC",
    "leads": "Leads", "cpl": "CPL", "conversions": "Ventas",
    "revenue": "Ingresos", "roas": "ROAS",
}
MISMATCH = {
    "ROAS": "El ROAS reportado no coincide con la relación entre inversión e ingresos.",
    "CTR": "El CTR reportado no coincide con la relación entre clics e impresiones.",
    "CPC": "El CPC reportado no coincide con la relación entre inversión y clics.",
    "CPL": "El CPL reportado no coincide con la relación entre inversión y leads.",
    "Frecuencia": "La frecuencia reportada no coincide con la relación entre impresiones y alcance.",
}
COMPARABLE = [
    ("investment", "Inversión"), ("impressions", "Impresiones"),
    ("reach", "Alcance"), ("clicks", "Clics"), ("ctr", "CTR"),
    ("cpc", "CPC"), ("leads", "Leads"), ("cpl", "CPL"),
    ("conversions", "Ventas"), ("revenue", "Ingresos"),
    ("roas", "ROAS"), ("frequency", "Frecuencia"),
]


def warning(kind: str, metric: str, reported: float | None, calculated: float | None,
            severity: str, message: str) -> dict:
    return {
        "type": kind, "metric": metric, "reported": reported,
        "calculated": calculated, "severity": severity, "message": message,
    }


def validity_warnings(data: CampaignInput) -> list[dict]:
    warnings = []
    for key, label in LABELS.items():
        value = getattr(data, key)
        if value is not None and value < 0:
            warnings.append(warning(
                "invalid_value", label, value, None, "critical",
                f"El valor de {label} es negativo, lo que no es posible en una campaña.",
            ))

    if data.clicks is not None and data.impressions is not None and data.clicks > data.impressions:
        warnings.append(warning(
            "impossible_relationship", "Clics / Impresiones",
            data.clicks, data.impressions, "critical",
            "Los clics reportados superan las impresiones, algo que no puede ocurrir.",
        ))
    if data.reach is not None and data.impressions is not None and data.reach > data.impressions:
        warnings.append(warning(
            "impossible_relationship", "Alcance / Impresiones",
            data.reach, data.impressions, "warning",
            "El alcance reportado supera las impresiones, algo que no debería ocurrir.",
        ))
    if data.conversions is not None and data.clicks is not None and data.conversions > data.clicks:
        warnings.append(warning(
            "impossible_relationship", "Ventas / Clics",
            data.conversions, data.clicks, "warning",
            "Las ventas reportadas superan los clics registrados en la campaña.",
        ))
    if re.search(r"venta|compra|ecommerce|conversion", data.objective or "", re.I):
        if data.revenue is None and data.conversions is None:
            warnings.append(warning(
                "missing_critical_metric", "Ventas / Ingresos",
                None, None, "info",
                "El objetivo es ventas, pero no se entregaron ventas ni ingresos: no se puede evaluar rentabilidad.",
            ))
    return warnings


def mismatch_warnings(data: CampaignInput, calculated: dict) -> list[dict]:
    warnings = []
    pairs = [
        ("ROAS", data.roas, calculated["roas"]),
        ("CTR", data.ctr, calculated["ctr"]),
        ("CPC", data.cpc, calculated["cpc"]),
        ("CPL", data.cpl, calculated["cpl"]),
        ("Frecuencia", data.frequency, calculated["frequency"]),
    ]
    for name, reported, computed in pairs:
        if reported is None or computed is None or within_tolerance(reported, computed):
            continue
        warnings.append(warning(
            "metric_mismatch", name, reported, computed, "warning",
            MISMATCH.get(name, f"El {name} reportado no coincide con el calculado."),
        ))
    return warnings


def build_comparisons(data: CampaignInput) -> list[dict]:
    comparisons = []
    for key, label in COMPARABLE:
        current = getattr(data, key)
        previous = getattr(data.previous, key)
        if current is None or previous is None:
            continue
        change = safe_divide(current - previous, previous)
        if change is None:
            continue
        comparisons.append({
            "metric": label, "current": current, "previous": previous,
            "changePercent": js_round(change * 100),
        })
    return comparisons


def analyze_metrics(data: CampaignInput) -> MetricsReport:
    calculated = calculate_metrics(data)
    return MetricsReport(
        calculated=calculated,
        comparisons=build_comparisons(data),
        warnings=validity_warnings(data) + mismatch_warnings(data, calculated),
    )


def format_number(value: float) -> str:
    """es-CO grouping and up to two decimals, like Intl.NumberFormat."""
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


CALCULATED_LABELS = [
    ("ctr", "CTR calculado (%)"),
    ("cpc", "CPC calculado"),
    ("cpl", "CPL calculado"),
    ("cpa", "Costo por venta calculado"),
    ("roas", "ROAS calculado"),
    ("conversionRate", "Tasa de conversión calculada (%)"),
    ("frequency", "Frecuencia calculada"),
]


def build_deterministic_block(report: MetricsReport) -> str:
    lines = [
        f"{label}: {format_number(report.calculated[key]) if report.calculated[key] is not None else 'No calculable con los datos disponibles'}"
        for key, label in CALCULATED_LABELS
    ]
    parts = [
        "MÉTRICAS CALCULADAS POR LA APLICACIÓN (ya verificadas, prioriza estos valores):\n"
        + "\n".join(lines),
    ]
    if report.comparisons:
        rows = []
        for item in report.comparisons:
            change = item["changePercent"]
            sign = "+" if change > 0 else ""
            rows.append(
                f'{item["metric"]}: {format_number(item["previous"])} → '
                f'{format_number(item["current"])} ({sign}{format_number(change)}%)'
            )
        parts.append("VARIACIONES CALCULADAS FRENTE AL PERIODO ANTERIOR:\n" + "\n".join(rows))

    if report.warnings:
        rows = []
        for item in report.warnings:
            detail = ""
            if item["reported"] is not None and item["calculated"] is not None:
                detail = (
                    f' (reportado: {format_number(item["reported"])} · '
                    f'calculado: {format_number(item["calculated"])})'
                )
            rows.append(f'[{item["severity"]}] {item["metric"]}: {item["message"]}{detail}')
        parts.append("ADVERTENCIAS DETECTADAS POR LA APLICACIÓN:\n" + "\n".join(rows))
    return "\n\n".join(parts)
