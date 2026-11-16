# Cómo trabajamos en este repositorio (Equipo 03)

## Reglas

1. Nadie sube cambios directo a `main`. Todo entra por pull request.
2. Una tarea = un issue = una rama = un pull request.
3. Cada pull request necesita las pruebas en verde y la aprobación de otra persona del equipo.
4. Cada contrato llega con su prueba en `tests/` (por ejemplo `tests/test_cl02_checkout.py`).
5. `db/01_modelo_fisico_expressya.sql` no se edita. Un cambio a la base va en un archivo nuevo `db/02_...sql`, `db/03_...sql`, y se avisa al PM antes de abrir el pull request.
6. Cada quien escribe solo en las tablas de su módulo. Si necesitas cambiar algo de otro módulo, llama a su función o pídeselo a su responsable.
7. Si usaste IA, llénalo en la plantilla del pull request: qué sugirió y qué aceptaron, cambiaron o rechazaron.
8. Un error encontrado se registra como issue con la plantilla «Defecto», aunque lo corrijas tú mismo.

## Nombres

- Rama: `<número de issue>-<descripción-corta>`, por ejemplo `3-cl02-checkout`.
- Commit: empieza con el contrato o escenario, por ejemplo `CL-02: valida que D no quede negativo`.
- Pull request: mismo título que el issue y `Closes #3` en la descripción.

## Ciclo de cada tarea

```bash
git switch main
git pull
git switch -c 3-cl02-checkout

python -m pytest -q
git add .
git commit -m "CL-02: crea la reserva de 300 s"
git push -u origin 3-cl02-checkout
```

Después abre el pull request en GitHub, pide revisión y espera la palomita verde de «Pruebas».

## Traer lo nuevo de `main` a tu rama

```bash
git switch main
git pull
git switch 3-cl02-checkout
git merge main
```

Si hay conflicto, abre los archivos marcados, deja la versión correcta, borra las marcas `<<<<<<<`, `=======`, `>>>>>>>`, corre las pruebas y haz commit.
