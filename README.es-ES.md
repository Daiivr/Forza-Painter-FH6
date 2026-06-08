<p align="center">
  <img src="docs/screenshots/forza-painter-fh6-showcase.png" alt="Presentación de Forza-Painter FH6">
</p>

# Forza-Painter FH6

**Herramienta de importación de vinilos para Forza Horizon 6.** Convierte imágenes en geometría de vinilo compatible con Forza, previsualiza el resultado e impórtalo en el Editor de Grupo de Vinilo de FH6 desde una sola aplicación de escritorio.

<p>
  <a href="README.md">English</a> |
  <a href="README.es-ES.md">Español</a> |
  <a href="README.es-MX.md">Español MX</a> |
  <a href="README.zh-CN.md">中文</a> |
  <a href="README.ko-KR.md">한국어</a>
</p>

<p>
  <code>v1.8.4</code> <code>Windows</code> <code>Forza Horizon 6</code> <code>GPU/OpenCL</code> <code>EXE de un solo archivo</code>
</p>

## Qué Hace

Forza-Painter FH6 está diseñado alrededor del flujo actual de vinilos en FH6:

- Genera geometry JSON desde imágenes PNG, JPG o BMP.
- Previsualiza el JSON generado antes de importarlo.
- Importa geometry JSON en una plantilla de vinilo FH6 desagrupada.
- Explora y descarga presets de la comunidad desde el Market integrado.
- Refina zonas importantes con Pintura Regional.
- Exporta e importa JSON experimentales de formas completas/type-code para investigación.
- Mantiene logs, datos de ejecución, vistas previas y diagnósticos dentro de carpetas locales de la app.

Los usuarios normales deben descargar el EXE desde [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases). No necesitas Python, entorno virtual ni el ZIP del código fuente salvo que vayas a desarrollar el proyecto.

## App Actual

Estas capturas fueron tomadas desde la interfaz de escritorio actual `v1.8.4`.

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-generate-json-current.png" alt="Pantalla Generar JSON">
      <strong>Generar JSON</strong><br>
      Añade imágenes, elige un preset de calidad, ajusta la generación y revisa el progreso.
    </td>
    <td width="50%">
      <img src="docs/screenshots/app-import-current.png" alt="Pantalla Importar">
      <strong>Importar</strong><br>
      Selecciona el proceso de FH6, introduce el número exacto de capas, previsualiza el JSON e importa.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-region-paint-current.png" alt="Pantalla Pintura Regional">
      <strong>Pintura Regional</strong><br>
      Genera una primera pasada, selecciona regiones clave y usa más capas donde el detalle importa.
    </td>
    <td width="50%">
      <img src="docs/screenshots/app-full-shapes-current.png" alt="Pantalla Exportar">
      <strong>Exportar</strong><br>
      Herramientas experimentales de exportación/importación de shape word FH6 para JSON de formas completas.
    </td>
  </tr>
</table>

## Flujo Dentro del Juego

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/fh6-template-ready.png" alt="Plantilla FH6 lista">
      <strong>Prepara una plantilla</strong><br>
      Abre el Editor de Grupo de Vinilo de FH6, carga una plantilla de esferas y desagrúpala.
    </td>
    <td width="50%">
      <img src="docs/screenshots/fh6-import-result.png" alt="Resultado importado en FH6">
      <strong>Importa el JSON</strong><br>
      Mantén el editor abierto mientras la app localiza la tabla editable de capas y escribe el diseño.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-import-preview.png" alt="Vista previa JSON de la app">
      <strong>Revisa la vista previa</strong><br>
      Las vistas previas JSON son útiles para comprobar capas, pero el resultado dentro del juego es la referencia final.
    </td>
    <td width="50%">
      <img src="docs/screenshots/fh6-car-applied.png" alt="Resultado aplicado al coche en FH6">
      <strong>Aplícalo al coche</strong><br>
      Una vez importado y guardado, usa el grupo de vinilo como cualquier otro diseño de FH6.
    </td>
  </tr>
</table>

## Inicio Rápido

1. Descarga `forza-painter-fh6-v1.8.4.exe` desde [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases).
2. Coloca el EXE en una carpeta normal con permisos de escritura, por ejemplo `Desktop\forza-painter-fh6`.
3. Ejecuta el EXE. Si la importación falla por permisos de proceso en Windows, ejecútalo como administrador.
4. En FH6, abre `Create Vinyl Group` / `Vinyl Group Editor`.
5. Carga una plantilla de esferas, desagrúpala y apunta el número exacto de capas que muestra el juego.
6. En Forza-Painter FH6, genera o añade un JSON, selecciona el proceso de FH6, introduce el número de capas e importa.

## Generar JSON

La página Generar JSON convierte imágenes en archivos de geometría compatibles con Forza usando el generador GPU/OpenCL incluido.

1. Añade una o más imágenes.
2. Elige un preset de calidad.
3. Opcionalmente abre `Ajustes de calidad` para modificar capas, resolución, muestras aleatorias y otros valores avanzados.
4. Inicia la generación y espera a que se actualicen la vista previa y los logs.
5. Usa el JSON con más capas que encaje en tu plantilla.

Los archivos generados se guardan junto a la imagen original. Una sola imagen puede producir checkpoints como `image.500.json`, `image.1000.json`, `image.3000.json` y un `image.json` final.

## Importar JSON

La página Importar escribe la geometría generada en la sesión actual del Editor de Grupo de Vinilo de FH6.

- La plantilla FH6 debe estar desagrupada antes de importar.
- El número de capas introducido en la app debe coincidir exactamente con el juego.
- Mantén FH6 en el Editor de Grupo de Vinilo mientras importas.
- No cambies de menú mientras la app escanea o escribe.
- Si Windows bloquea el acceso al proceso, reinicia la app como administrador.

FH6 necesita algunas capas extra de límite para guardar y aplicar correctamente el área del diseño. Por ejemplo, un JSON de 1000 capas debería usar una plantilla de al menos 1004 capas; una plantilla de 3000 capas suele dejar unas 2996 capas dibujables.

## Pintura Regional

Pintura Regional sirve para imágenes donde algunas zonas necesitan más detalle que otras. Genera una primera pasada, permite seleccionar un rectángulo o elipse y después usa capas adicionales solo en esa región.

Herramientas actuales:

- Presupuesto de capas para primera pasada y pasadas regionales.
- Selecciones rectangulares y elípticas.
- Controles para arrastrar, redimensionar, rotar y usar la rueda del ratón.
- Pestañas de vista previa y mapa de calor.
- Historial de pasadas, seguimiento de capas restantes y exportación del JSON resultante.

## Market

La página Importar incluye un botón Market para presets de painter6.com. Puedes explorar diseños, previsualizar presets, descargar geometry JSON y añadir el JSON descargado directamente a la lista de importación.

## Exportar

Exportar es experimental. Está pensado para JSON type-code FH6 exportados o creados a mano, no para la geometría normal de elipses generada por la app.

- Usa la palabra de forma FH6 de 16 bits en el desplazamiento de capa `0x7A`.
- Exporta campos visuales estables como posición, escala, rotación, sesgo, color, datos de máscara/banner y palabra de forma.
- Evita punteros de recursos volátiles como `0xA8`.
- Usa recursos de vinilo FH6 incluidos para vistas previas cuando están disponibles.

Usa la página Importar estándar para geometry JSON normal generado por la app.

## Carpetas de Ejecución

El EXE de un solo archivo extrae sus archivos internos temporalmente y escribe datos normales de la app junto al EXE:

- `runtime/`: logs, vistas previas de generación, sesiones de Pintura Regional, descargas del Market y archivos temporales.
- `webui-data/`: preferencias locales y caché de sesión/localizador FH6.

Puedes borrar estas carpetas con la app cerrada si quieres restablecer los datos locales.

## Solución de Problemas

- **La importación no inicia:** ejecuta la app como administrador y confirma que el editor de FH6 está abierto.
- **No se encuentra la plantilla:** desagrupa la plantilla, introduce el número exacto de capas y permanece en el editor durante el escaneo.
- **El resultado se ve borroso:** sube las capas de salida y `Random samples`; valores por encima de `200000` suelen mejorar la claridad final.
- **La vista previa difiere de FH6:** las vistas previas JSON son aproximadas porque FH6 conserva tamaños decimales de elipse que la vista previa simplifica.
- **Error GPU/OpenCL:** actualiza los drivers gráficos de NVIDIA, AMD o Intel.
- **Necesitas ayuda depurando:** usa `Exportar registro detallado` y adjunta el log al abrir un issue.

## Desarrollo

Ejecutar desde código fuente está pensado principalmente para desarrollo y pruebas:

```powershell
install_dependencies.bat
start_app.bat
```

Archivos útiles:

- `src/app.py`: interfaz de escritorio y flujos de trabajo.
- `src/generator_backend.py`: integración con el comando del generador.
- `src/import_readiness.py`: comprobaciones previas a la importación.
- `src/region_painter/`: módulos del flujo de Pintura Regional.
- `scripts/make_exe_release.ps1`: empaquetado de releases.
- `CHANGELOG.md`: historial completo de versiones.

## Recursos

- Releases: [github.com/Daiivr/Forza-Painter-FH6/releases](https://github.com/Daiivr/Forza-Painter-FH6/releases)
- Vídeo de importación: [bilibili.com/video/BV1hG5Z6nENZ](https://www.bilibili.com/video/BV1hG5Z6nENZ)
- Generador GPU incluido: [zjl88858/forza-painter-geometrize-gpu](https://github.com/zjl88858/forza-painter-geometrize-gpu)
- Changelog completo: [CHANGELOG.md](CHANGELOG.md)

## Licencia

Consulta [LICENSE](LICENSE), [LICENSE.custom-importer](LICENSE.custom-importer) y [LICENSE.kloudys-custom-importer](LICENSE.kloudys-custom-importer).
