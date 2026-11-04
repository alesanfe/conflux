## Qué cambia y por qué

<!-- Una línea por cambio. Si es fix, enlaza el issue. -->

## Verificación

- [ ] `python3 -m unittest discover -s tests -t .` — suite completa OK
- [ ] `scripts/check.sh` — linters + gates OK
- [ ] Tests nuevos para el cambio (y cubren los bordes, no solo el happy path)
- [ ] Si es bug fix: el test **fallaba antes** del fix (failing-first — pega el output rojo)
- [ ] `docs/openapi.yaml` actualizado si toca rutas
- [ ] README Features actualizado si añade feature
- [ ] Sin secretos, sin red real en tests, escrituras de estado atómicas

## Riesgos / rollback

<!-- Qué puede romperse, cómo volver atrás -->
