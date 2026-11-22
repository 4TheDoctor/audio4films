# audio4films

**Pon el doblaje de una versión sobre el vídeo de otra mejor, sincronizado y sin recodificar el vídeo.**

*[Read in English](README.md)*

Empezó un domingo por la noche. La versión 4K Dolby Vision + HDR10+ de una serie solo estaba en
inglés, y el doblaje en castellano solo venía con un vídeo peor. audio4films junta las dos: mide la
distancia entre las dos pistas de audio, se niega a adivinar cuando las versiones no coinciden, hace
el mux sin pérdida y comprueba el resultado antes de darlo por bueno.

```text
2/4 Measuring the offset
    Original time   Dub time     Offset      Match clarity
         139.7s     139.699s     +0.035s    6.05  used
         512.4s     512.321s     +0.034s    5.96  used
         885.0s     884.943s     +0.034s    5.67  used
        1257.6s    1257.565s     +0.034s   13.04  used
        1630.2s    1630.187s     +0.034s    4.19  used
        2002.8s    2002.809s     +0.034s    3.26  used
  Verdict: constant. 6 of 6 clear points agree within 50 ms.
3/4 Muxing (video and original audio are copied untouched)
4/4 Verifying the result
    Dub vs original audio at 5 points: -0.5 to -0.3 ms (limit ±20 ms)
    Dub stored next to the video: largest gap 0.4 MB (limit 50 MB, checked at 3 points)
    Dolby Vision: source yes, output yes
    HDR10+: source yes, output yes
Result: PASSED
```

*(Salida real de un episodio 4K de 39 minutos. El original se leyó por HTTP y el proceso tardó 4 minutos.)*

## Qué hace

- **Elige las pistas por idioma**, no por posición. En una versión con varios idiomas, la primera pista de audio no suele ser la que quieres.
- **Mide el desfase en 6 puntos** correlacionando la música y los efectos, que las dos versiones comparten. Si el desfase no es igual en todas partes (una escena cortada, otra velocidad), **se detiene y te dice por qué** en vez de generar un archivo que se desincroniza.
- **Admite doblajes PAL** (25 fps frente a 23,976): deshace la aceleración y el cambio de tono.
- **Hace el mux sin pérdida**: copia el vídeo, el audio original y los subtítulos, y conserva Dolby Vision y HDR10+. El doblaje queda como pista por defecto, con sus subtítulos forzados activados, y el idioma original a un clic.
- **Guarda el doblaje junto al vídeo** dentro del archivo, para que al saltar en la línea de tiempo nunca se pierda el doblaje.
- **Verifica** el archivo final: doblaje frente a original en 5 puntos, entrelazado, Dolby Vision y HDR10+.
- **Lo sirve a Stremio** en la tele por la red de casa (opcional).

Las entradas pueden ser archivos locales o URLs HTTP(S).

## Instalación

Necesita [FFmpeg](https://ffmpeg.org) 6 o superior (`brew install ffmpeg` / `apt install ffmpeg`) y Python 3.10+.

```bash
uv tool install git+https://github.com/4TheDoctor/audio4films
# o: pipx install git+https://github.com/4TheDoctor/audio4films
```

### Como skill de Claude Code

```text
/plugin marketplace add 4TheDoctor/audio4films
/plugin install audio4films@audio4films
```

Luego pídele a Claude algo como *"añade el audio en castellano de doblaje.mkv a peli-4k.mkv"*. La
skill maneja la CLI y no da nada por terminado hasta que pasa la verificación. La CLI tiene que estar
instalada igualmente (ver arriba).

## Uso

```bash
# Todo de una vez: copiar las pistas del doblaje, sincronizar, hacer el mux y verificar.
audio4films run peli-4k.mkv peli-doblaje.mkv --vo-lang eng --dub-lang spa

# Ver antes las pistas.
audio4films probe peli-4k.mkv peli-doblaje.mkv

# Paso a paso.
audio4films sync   peli-4k.mkv peli-doblaje.mkv          # muestra el desfase y un veredicto
audio4films mux    peli-4k.mkv peli-doblaje.mkv --offset 0.034
audio4films verify ~/Movies/audio4films/peli-4k.spa+eng.mkv --source peli-4k.mkv
```

La salida va a `~/Movies/audio4films` (o `~/Videos/audio4films`). Se cambia con `--out-dir` o `A4F_OUT_DIR`.

| Código de salida | Significado |
| --- | --- |
| 0 | Hecho y verificado |
| 1 | Error (falta un archivo, una pista o una herramienta) |
| 2 | No es seguro sincronizar con un único desplazamiento (`step`, `drift` o `unreliable`); no se escribió nada |
| 3 | Escrito, pero la verificación falló. Lee las líneas PROBLEM |

### Una temporada entera

```bash
for e in 06 07 08 09 10; do
  audio4films run "Serie.S03E$e.2160p.mkv" "Serie.S03E$e.doblaje.mkv" \
    --stremio-id "tt1234567:3:${e#0}" --title "Serie S03E$e" --group serie-s03
done
```

Mide cada episodio por separado. En una temporada real los desfases fueron +34, +161, +34, +84 y +53 ms.

### Verlo en Stremio

```bash
audio4films serve
# En Stremio: Complementos -> Agregar complemento -> escribe http://<ip-de-este-ordenador>:7878/manifest.json
```

Escribe la dirección `http://` a mano, porque los enlaces `stremio://` fuerzan HTTPS. Los problemas
habituales están en [skills/audio4films/references/stremio.md](skills/audio4films/references/stremio.md) (en inglés).

## Cómo funciona

Lo tienes en [how-sync-works.md](skills/audio4films/references/how-sync-works.md) (en inglés). En
resumen: filtra las dos pistas entre 60 y 1500 Hz, correlaciona ventanas de 45 segundos en 6
puntos, acepta solo un desfase constante, desplaza el doblaje con `-itsoffset`, hace el mux con
entrelazado estricto y vuelve a medir el resultado.

## Límites

- Las dos versiones deben ser el mismo montaje. Si no lo son, se detiene (`step`) y no intenta empalmar.
- El doblaje debe tener la misma música y los mismos efectos que el original. Un doblaje remezclado o con otra banda sonora no coincidirá.
- Los doblajes PAL con el tono ya corregido en el máster pueden no correlacionar.
- Stremio recibe el vídeo por HTTP sin cifrar en tu red local, y el ordenador tiene que estar encendido.

## Herramientas parecidas

- [syncstart](https://github.com/rpuntaie/syncstart), [sync-offset-tool](https://pypi.org/project/sync-offset-tool/) y [AudioSync](https://github.com/zzattack/AudioSync) miden el desfase de audio entre dos archivos.
- [ffsubsync](https://github.com/smacke/ffsubsync) usa una correlación parecida para subtítulos.

audio4films añade la elección de pistas por idioma, comprobaciones en varios puntos que rechazan los desplazamientos inseguros, un mux sin pérdida que conserva el HDR, la verificación y la reproducción en Stremio.

## Aviso legal

Úsala con contenido que tengas derecho a usar. Trabaja con los archivos y URLs que le das. No busca
ni descarga contenido.

## Licencia

[MIT](LICENSE)
