# QA Test Recorder

Aplicación de escritorio para grabar recorridos web, capturar evidencias, generar casos de prueba y reproducir flujos.

## Inicio

```powershell
python -m app.main
```

## Estructura

```text
app/
  main.py                         Punto de entrada Tkinter
  config.py                       Rutas y configuración compartida
  audit/                          Revisiones por pantalla durante la reproducción
  automation/                     Reproducción de flujos guardados y opciones por ambiente
  evidence/                       Captura de screenshots
  models/                         Modelos de dominio (action.py)
  recorder/                       Captura, sanitización y transformación de eventos
  reporting/                      Generación de documentos Word
  services/                       Casos de uso que coordinan módulos
  storage/                        Persistencia JSON
  ui/                             Ventana y componentes visuales
    case_matrix_panel.py          Carga y selección de casos Excel

scripts/
  inspect_latest_execution.py     Diagnóstico manual de la última ejecución

docs/
  architecture.md                 Capas, criterios de diseño y calidad

data/
  executions/                     Artefactos técnicos de cada grabación
  flows/                          Flujos reutilizables
  test_cases/                     Casos de prueba Word
```

## Dónde tocar cada funcionalidad

- Cambiar la apariencia o botones: `app/ui/main_window.py`.
- Cambiar la experiencia de la matriz Excel: `app/ui/case_matrix_panel.py`.
- Cambiar captura de clicks, inputs o screenshots: `app/recorder/browser_recorder.py`.
- Cambiar qué valores se protegen antes de guardarse: `app/recorder/sensitive_data.py`.
- Cambiar agrupación y descripción de acciones QA: `app/recorder/event_parser.py`.
- Cambiar reproducción automática: `app/automation/flow_runner.py`.
- Cambiar freno de pago, ambientes o datos de prueba: `app/automation/run_settings.py`.
- Cambiar las revisiones por pantalla (consola, red, accesibilidad, tiempos): `app/audit/page_checks.py`.
- Cambiar estructura del Word: `app/reporting/test_case_report.py`.
- Cambiar el reporte HTML de cada corrida: `app/reporting/run_report.py`.
- Cambiar qué archivos se crean al finalizar: `app/services/artifact_service.py`.
- Cambiar carpetas de salida: `app/config.py`.
- Cambiar persistencia JSON: `app/storage/json_repository.py`.

## Flujo de una ejecución

1. La UI crea un `BrowserRecorder`.
2. El recorder captura eventos y screenshots.
3. Al finalizar, `TestArtifactService` genera `actions.json`, `qa_actions.json`, el flujo reutilizable y el Word.
4. `FlowRunner` puede reproducir el último flujo guardado.

## Reproducción por ambiente

Al reproducir un flujo se elige un **ambiente** en el panel de flujos guardados. Cada ambiente define si el freno de pago está activo y qué datos de prueba se usan. Los dos archivos viven en `data/` y no se suben al repositorio; hay ejemplos en `docs/examples/`.

- `data/run_config.json`: `pasos_bloqueados` (texto o selector de los clics que no deben ejecutarse, por ejemplo "Continuar al pago") y, por ambiente, `freno_pago`. Con el freno activo, la reproducción se detiene antes de un paso bloqueado y lo marca como `detenido_por_seguridad`. **Sin ambiente, o con un ambiente sin `"freno_pago": false`, el freno queda activo.** Desactívelo solo en ambientes con pasarelas de pago en sandbox.
- `data/test_data.json`: valores para los campos, por selector, id, `name` o placeholder. Reemplazan los valores grabados y los `[MASKED]`; los del ambiente tienen prioridad sobre `campos`. Un valor `env:NOMBRE` se lee de la variable de entorno `NOMBRE`, útil para contraseñas.

En el mismo panel se elige el **navegador** (chromium, firefox o webkit, que equivale a Safari), el **dispositivo** (Escritorio con el mismo tamaño que el grabador, iPhone 13, Pixel 7 o iPad) y si corre en **segundo plano**. Firefox y WebKit se instalan una vez con `python -m playwright install firefox webkit`.

Cada reproducción crea `data/runs/run_<flujo>_<navegador>_<dispositivo>_<fecha>/` con:

- `report.html`: el reporte de la corrida, que la app abre al terminar. Incluye el resultado (completado, detenido por seguridad o fallido, con el paso, el error y una captura), los hallazgos, las pantallas recorridas con sus tiempos y capturas, los errores de red y de consola agrupados, y la accesibilidad ordenada por impacto.
- `result.json` y `findings.json`: los mismos datos en JSON, para otras herramientas.
- `pantalla_NNN.png`, `fallo.png` o `detenido.png`: capturas. Pueden mostrar datos personales; no las comparta fuera del equipo.
- `trace.zip`, solo si se marca **Guardar traza**. Es opcional porque en sitios grandes guardarla puede tardar varios minutos; su valor por defecto sale de `"trazas"` en `data/run_config.json`.

Solo puede correr una reproducción a la vez desde la UI.

Durante la reproducción se revisa cada pantalla nueva y el resultado queda en `findings.json` de la corrida:

- **Errores de consola** y excepciones de JavaScript.
- **Errores de red**: respuestas 4xx/5xx y peticiones que no se completaron (las cancelaciones normales al navegar se descartan).
- **Accesibilidad** con axe-core, una vez por URL.
- **Tiempos de carga** (TTFB, DOMContentLoaded, load y LCP cuando el navegador lo mide); una pantalla es lenta si supera `carga_lenta_ms` o no termina de cargar.

En `revisiones` de `data/run_config.json` se puede apagar la accesibilidad, cambiar el umbral de carga y listar textos en `ignorar` (dominios de terceros o mensajes conocidos). Las URLs se guardan sin los valores de la query para no exponer correos ni tokens. El conteo también queda en `findings` de `result.json`. Estas revisiones no cambian el estado del flujo: un flujo puede completarse y tener hallazgos.

La traza se abre con:

```powershell
python -m playwright show-trace data/runs/<carpeta>/trace.zip
```

## Instalación

```powershell
python -m pip install -r requirements.txt
```

`recorder.py` es una implementación anterior mantenida solo por compatibilidad. El código activo está dentro de `app/`.

## Calidad y SonarQube

Para preparar el analisis local:

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s test
python -m bandit -r app -ll
python -m coverage run -m unittest discover -s test
python -m coverage xml
```

El archivo `sonar-project.properties` configura SonarQube para analizar `app/`, reconocer `test/` y leer `coverage.xml`. Instala SonarScanner aparte y configura `SONAR_HOST_URL` y `SONAR_TOKEN` como variables de entorno; no guardes credenciales en el repositorio. Consulta [docs/architecture.md](docs/architecture.md) para las responsabilidades por capa y el uso adecuado de POM.

El script de diagnóstico manual se ejecuta con:

```powershell
python scripts/inspect_latest_execution.py
```

## Crear ejecutable de Windows

En PowerShell, desde la raíz del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt pyinstaller
.\build.ps1
```

El resultado queda en `dist/QA-Test-Recorder/`. Debe copiarse la carpeta completa, no solo el `.exe`.

La PC destino debe tener instalado el **Microsoft Visual C++ Redistributable 2015-2022 x64**. El mensaje `python313.dll: no se puede encontrar el módulo especificado` suele indicar que falta ese runtime o que se está copiando solo una parte de la distribución.
