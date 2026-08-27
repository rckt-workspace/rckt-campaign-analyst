# RCKT Campaign Analyst

RCKT Campaign Analyst es una herramienta de inteligencia artificial diseñada para transformar métricas de campañas digitales en análisis ejecutivos comprensibles y accionables.

## Objetivo

El usuario ingresa resultados de campaña (texto libre o formulario rápido) y obtiene:

- estado general;
- resumen ejecutivo;
- principales hallazgos;
- recomendaciones;
- información faltante;
- advertencias de consistencia.

## Stack

- Lovable
- Lovable Cloud
- Lovable AI
- GitHub

## Arquitectura

Single Agent Architecture: un único agente especializado ("Analista IA de Campañas") interpreta las métricas suministradas.

Antes de llegar al agente, la aplicación normaliza los valores (formatos latinoamericanos), calcula métricas derivadas (CTR, CPC, CPL, CPA, ROAS, tasa de conversión, frecuencia), compara periodos y detecta inconsistencias matemáticas.

No utiliza multiagente, RAG, memoria, historial ni APIs externas.
