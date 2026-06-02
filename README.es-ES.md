<p align="center">
  <img src="https://github.com/user-attachments/assets/d4f48f71-d76e-4ffe-9fb1-0b075d79bf05" alt="logo de forza-painter FH6" width="720">
</p>

<h1 align="center">forza-painter FH6</h1>

<p align="center">
  <strong>Generador e importador de imagenes para Vinyl Groups de Forza Horizon 6.</strong>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.es-ES.md">Español</a> ·
  <a href="README.zh-CN.md">中文</a> ·
  <a href="README.ko-KR.md">한국어</a>
</p>

<p align="center">
  <code>v1.7.0</code> · <code>Windows</code> · <code>Forza Horizon 6</code> · <code>GPU/OpenCL</code> · <code>One-file EXE</code>
</p>

Convierte imagenes PNG/JPG/BMP en capas de Vinyl Group para Forza Horizon 6. La app se encarga de generar, previsualizar e importar todo desde una sola ventana de escritorio; los usuarios normales no necesitan Python, `.venv`, archivos batch ni direcciones de memoria manuales.

> **Descargar el EXE:** descarga `forza-painter-fh6-v1.7.0.exe` desde [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases) y ejecútalo directamente.

> **Si el resultado se ve borroso:** sube primero `Random samples`. Los valores por encima de **200000** suelen mejorar mucho la calidad; valores mas altos se ven mas claros, pero tardan bastante mas en generarse.

> **La importacion puede tardar:** desde v1.4.1 la app prueba varios localizadores de plantillas de FH6 y puede tardar hasta 5 minutos en encontrar la tabla de capas segura. Mantén FH6 en Vinyl Group Editor, no cambies de menu y exporta un registro detallado si sigue fallando.

| Que hace | Detalles |
| --- | --- |
| Generar JSON | Convierte imagenes en geometry JSON con el generador GPU/OpenCL incluido. |
| Vista previa | Muestra la imagen original y la geometria generada dentro de la app. |
| Importar a FH6 | Importa JSON al Vinyl Group Editor de FH6 que esta abierto actualmente. |
| Flujo seguro para FH6 | Localiza y verifica automaticamente la tabla de capas editable antes de escribir. |
| Comprobacion de actualizaciones | Busca nuevas versiones al iniciar y muestra las notas del changelog cuando hay una disponible. |

## Inicio rapido

1. Descarga `forza-painter-fh6-v1.7.0.exe` desde [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases).
2. Pon el EXE en una carpeta normal con permisos de escritura, por ejemplo `Desktop\forza-painter-fh6`.
3. Haz doble clic en el EXE. Para importar en FH6, ejecútalo como administrador si Windows bloquea el acceso al proceso.
4. En FH6, abre `Create Vinyl Group` / `Vinyl Group Editor`, carga una plantilla de esferas y luego usa `Ungroup`.
5. En la app, genera el JSON, abre la pagina `Import`, escribe la cantidad exacta de capas de la plantilla y luego importa.

No descargues el ZIP automatico de `Source code` de GitHub salvo que vayas a desarrollar el proyecto. Los usuarios normales solo necesitan el `.exe`.

## Vista previa

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/screenshots/app-import-preview.png" alt="Pagina de importacion de la app"><br>
      <strong>Pagina de importacion de la app</strong>
    </td>
    <td align="center" width="50%">
      <img src="docs/screenshots/fh6-template-ready.png" alt="Plantilla lista en FH6"><br>
      <strong>Plantilla lista en FH6</strong>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="docs/screenshots/fh6-import-result.png" alt="Resultado importado en FH6"><br>
      <strong>Resultado importado</strong>
    </td>
    <td align="center" width="50%">
      <img src="docs/screenshots/fh6-car-applied.png" alt="Resultado aplicado al coche en FH6"><br>
      <strong>Aplicado al coche</strong>
    </td>
  </tr>
</table>

## Generar JSON

1. Abre la pagina `Generate JSON`.
2. Haz clic en `Add images` y elige imagenes PNG/JPG/BMP.
3. Selecciona un preajuste de calidad.
4. Opcional: activa `Use custom settings` para cambiar las capas de salida, la resolucion, las muestras aleatorias y las muestras mutadas.
5. Haz clic en el boton fijo inferior `Start generating`.
6. Espera a que se actualicen la vista previa y los registros.

Los archivos generados se guardan junto a la imagen original, por ejemplo `image.500.json`, `image.1000.json` e `image.3000.json`.

Una imagen puede generar varios archivos JSON de punto de control. Prefiere el JSON con mas capas que coincida con tu plantilla; por ejemplo, usa `image.3000.json` o el `image.json` final con una plantilla de 3000 capas. Importar un JSON de 500 capas en una plantilla de 3000 capas se vera borroso.

| Preajuste | Capas de salida | Random samples | Uso |
| --- | ---: | ---: | --- |
| extremely fast | 500 | 30000 | Revisiones rapidas de composicion |
| fast | 1000 | 60000 | Borradores rapidos utilizables |
| balanced | 1800 | 120000 | Valor recomendado por defecto |
| slow | 2500 | 220000 | Calidad final; empieza a usar el rango de calidad 200k+ |
| super slow | 3000 | 350000 | Mejor claridad, muy lento |

## Importar JSON

1. Inicia FH6 y mantén abierto `Vinyl Group Editor`.
2. Carga o crea una plantilla hecha con muchas capas simples de esfera.
3. Usa `Ungroup` en la plantilla y recuerda la cantidad exacta de capas que muestra el juego.
4. En la app, abre `Import`, haz clic en `Refresh` y selecciona `forzahorizon6.exe`.
5. Escribe la cantidad exacta de capas de la plantilla.
6. Añade el `.json` generado o haz clic en `Use generated JSON`.
7. Deja vacios los campos avanzados de direccion y haz clic en `Import JSON`.

FH necesita 4 capas extra de limite para guardar la portada y aplicar los limites correctamente. Ejemplo: un JSON de 1000 capas deberia usar al menos una plantilla de 1004 capas; una plantilla de 3000 capas puede importar unas 2996 formas dibujables.

## Reglas importantes

- La plantilla de FH6 debe estar desagrupada antes de importar.
- La cantidad de capas en la app debe coincidir exactamente con la del juego.
- No cambies de menu en el juego mientras se importa.
- Despues de reiniciar FH6, recargar la plantilla o cambiar la cantidad de capas, importa de nuevo con la nueva cantidad correcta.
- Si el JSON tiene menos capas que la plantilla, las capas de plantilla sin usar se ocultan.
- Si el JSON tiene mas capas que la plantilla, las formas extra se recortan.
- Los fondos transparentes de PNG no se importan como fondos visibles.

## Archivos de ejecucion

El EXE de un solo archivo extrae temporalmente sus archivos internos y guarda los datos normales de ejecucion fuera del EXE. La app muestra las rutas exactas en el registro de inicio y en la pagina `Tools`.

Carpetas externas esperadas junto al EXE:

- `runtime/`: registros, datos de sesion generados y archivos temporales de la app.
- `webui-data/`: cache local del navegador/UI.

Estas carpetas se pueden eliminar cuando la app esta cerrada si quieres reiniciar los datos locales de ejecucion.

## Solucion de problemas

- **El EXE no importa en FH6:** cierra la app y ejecuta el EXE como administrador.
- **Error GPU/OpenCL:** actualiza los drivers graficos de NVIDIA/AMD/Intel. El generador incluido usa OpenCL.
- **No se puede localizar la plantilla:** confirma que estas en Vinyl Group Editor, que la plantilla esta desagrupada, que la cantidad de capas es exacta y que no se cambio de menu durante el escaneo.
- **El resultado importado se ve borroso:** usa un JSON con mas capas o aumenta `Output layers` / `Random samples`.
- **Necesitas ayuda para depurar:** usa `Export detailed log` en la app y adjunta el registro a un issue.

## Recursos

- Video guia de importacion: https://www.bilibili.com/video/BV1hG5Z6nENZ
- Fuente/referencia del generador GPU incluido: https://github.com/zjl88858/forza-painter-geometrize-gpu
- Changelog completo: [CHANGELOG.md](CHANGELOG.md)

## Changelog

Aqui solo se mantienen entradas de lanzamiento con version. Consulta [CHANGELOG.md](CHANGELOG.md) para el changelog usado por el aviso de actualizacion de la app.

### v1.7.0 / 2026-06-02

- Se actualizo la version de la app a `v1.7.0`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.7.0.exe`.
- Se añadio un Market dentro de la app en la pestaña Import para explorar presets de painter6.com, previsualizar diseños, abrir el preset seleccionado en el navegador, descargar geometry JSON y añadirlo automaticamente a la pestaña Import.
- Las descargas del Market ahora reutilizan un JSON valido existente en `runtime/market-downloads` en vez de descargar el mismo preset otra vez.
- Los JSON existentes del Market se validan contra el hash del preset cuando esta disponible, y las descargas reutilizadas muestran su propio mensaje de confirmacion traducido.
- Al cambiar de idioma con el modal del Market abierto, ahora se actualizan inmediatamente los detalles del preset, contadores, descripciones vacias, avisos y el boton traducido del Market.
- Se mejoro la busqueda del Market, incluyendo busquedas estrictas `#tag` que solo coinciden con etiquetas reales.
- Se limpio el selector de imagenes de Generate JSON y la tarjeta de calidad, incluyendo el resumen de capas seleccionadas.
- Se añadieron bordes de acento mas claros y mejor comportamiento topmost para los modales tematizados.
- Se añadieron traducciones para los nuevos textos de market, imagenes, calidad, modales y estados.

### v1.6.8 / 2026-05-28

- Se actualizo la version de la app a `v1.6.8`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.6.8.exe`.
- Se conservaron los valores decimales de ancho/alto de elipses de los ultimos cambios de GitHub `main`, mejorando la precision de importacion dentro del juego.
- Se añadio una nota en el panel de vista previa indicando que v1.6.8 prioriza mejores resultados dentro del juego, mientras las vistas previas siguen siendo aproximadas.
- Se mejoro el renderizado de vista previa JSON con supersampling para reducir la degradacion de elipses con tamaños decimales.

### v1.6.7 / 2026-05-27

- Se actualizo la version de la app a `v1.6.7`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.6.7.exe`.
- Se actualizo el generador GPU incluido al upstream `canary-26052702`.
- Se reemplazaron numeros magicos de escala de importacion FH6 con constantes nombradas para los tamaños base de circulo y rectangulo.
- Se mejoro la estimacion de ETA de generacion para salida con buffer del generador y cambios de velocidad.

### v1.6.6 / 2026-05-26

- Se actualizo la version de la app a `v1.6.6`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.6.6.exe`.
- Se añadieron traducciones de chino tradicional para la UI y se mejoro el diseño del selector de idioma.
- Se corrigio el preprocesamiento `luma_band` para imagenes RGB, se hizo mas segura la escritura de imagenes preprocesadas y se añadieron pruebas para datos de geometria/color.
- Se empaquetaron OpenCV y NumPy dentro del EXE de un solo archivo para que el preprocesamiento `luma_band` funcione en builds de lanzamiento.
- Import ahora requiere la cantidad de capas de la plantilla FH6 antes de empezar.
- Se refactorizaron modulos centrales con excepciones tipadas y utilidades compartidas.

### v1.6.5 / 2026-05-25

- Se actualizo la version de la app a `v1.6.5`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.6.5.exe`.
- Se actualizo el generador GPU incluido al upstream `v1.2-Canary-20260525`.
- Los presets incluidos ahora usan `forceOpaqueShapes = false` por defecto.
- Se redujo el overhead de la app principal durante la generacion usando un entorno de generador saneado, polling de archivos mas lento y escrituras de vista previa menos frecuentes en el preset mas pesado.
- Se corrigio el seguimiento de salida generada cuando el preprocesamiento crea una imagen de entrada separada.

### v1.6.1 / 2026-05-24

- Se actualizo la version de la app a `v1.6.1`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.6.1.exe`.
- Se desactivo el preprocesamiento `luma_band` por defecto en los presets incluidos.
- Import ya no reutiliza datos viejos de sesion FH6 desde `webui-data`; vuelve a localizar la plantilla actual antes de escribir.
- Las vistas previas JSON ahora usan una sola ruta de renderizado estable para evitar diferencias de distorsion de elipses entre entornos de EXE empaquetado.

### v1.6.0 / 2026-05-24

- Se actualizo la version de la app a `v1.6.0`; los paquetes de lanzamiento ahora usan `forza-painter-fh6-v1.6.0.exe`.
- Se actualizo el generador GPU incluido al upstream `canary-26052401`.
- Se añadio soporte para presets upstream `errorGridSize`.
- Se integro el ajuste upstream del algoritmo que evita el sobrepaso en areas transparentes.
- Se mejoro de forma notable la calidad de generacion para la elipse grande en la parte inferior de imagenes transparentes.

### v1.5.4 / 2026-05-23

- Se corrigio el escalado de vista previa para imagenes fuente de alta resolucion, PNGs de vista previa del generador y vistas previas JSON para que la imagen completa encaje en el panel actual sin estirarse.
- Se corrigio el renderizado de elipses rotadas tipo 16 en vistas previas JSON para que las vistas previas de la pagina Import ya no aplasten ni roten incorrectamente los trazos de elipse.

### v1.5.3 / 2026-05-22

- Se añadio importacion de presets personalizados compatible con EXE, eliminacion de listas de imagenes/JSON, reutilizacion de checkpoints, nombres de salida mas seguros y fallback de vista previa con Pillow.

### v1.5.2 / 2026-05-22

- Se añadio un verdadero EXE de un solo archivo para que los usuarios normales ya no necesiten Python, `.venv` ni archivos auxiliares.
- El EXE con GUI puede relanzarse a si mismo en modo helper oculto para importar y sondear memoria de FH6.
- La pagina Tools y el registro de inicio ahora muestran las ubicaciones externas de runtime/cache.

### v1.5.1 / 2026-05-22

- Se corrigio la instalacion de dependencias de inicio cuando existe un `.venv` del proyecto pero su Python no tiene `pip`.
- Se mejoraron los diagnosticos del script de inicio para extracciones incompletas del paquete fuente.

### v1.5.0 / 2026-05-22

- Se actualizo el generador GPU/OpenCL incluido al upstream `canary-26052102`.
- Se añadio el algoritmo upstream de evaluacion por work-group desde PR #4 para acelerar la evaluacion de candidatos en GPU.
- Se añadio comprobacion de actualizaciones al iniciar, `CHANGELOG.md` en la raiz y la UI de escritorio oscura.

### v1.4.1 / 2026-05-21

- La localizacion automatica de plantillas FH6 ahora prueba las estrategias de escaneo v1.3 y v1.4 antes de rendirse.
- Se añadio un localizador fallback de vtable RTTI y se aumento el presupuesto de espera de localizacion automatica.

### v1.4.0 / 2026-05-21

- Se añadio exportacion de registro detallado limitada a 50000 caracteres.
- Se mejoro la localizacion automatica de plantillas FH6 para regiones de memoria escribibles grandes.

### v1.3.0 / 2026-05-21

- Se actualizo el generador GPU/OpenCL incluido al upstream `canary-26052101`.
- Se añadio el arreglo upstream de seleccion de dispositivo GPU y registro del dispositivo seleccionado.

### v1.2.0 / 2026-05-20

- Se actualizo el generador GPU/OpenCL incluido al upstream `canary-26052001`.
- Se añadio `forceOpaqueShapes = true` a configuraciones de generacion incluidas y personalizadas.

### v1.1.1 / 2026-05-20

- Se añadio gestion centralizada de version para la ventana de la app, CLI y nombres de paquetes de lanzamiento.
- Se reorganizo la estructura del repositorio y el empaquetado de lanzamiento.
