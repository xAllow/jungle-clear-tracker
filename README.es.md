<div align="center">

# Jungle Clear Tracker

**¿Cuánto tardas de verdad en tu primer clear?**
Tiempo exacto de cada campamento, orden, scuttles y un mapa de tus primeros 7 minutos, en cada partida de jungla, de forma automática.

![Windows](https://img.shields.io/badge/Windows-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Licencia: MIT](https://img.shields.io/badge/Licencia-MIT-green)

[English](README.md) · **Español**

</div>

![Partidas](docs/games.png)

## Qué hace

- ⏱️ **Tiempo del primer clear** con ~0,5 s de precisión, y cuándo murió cada campamento
- 🧭 **Orden de campamentos**, incluidos los que haces a la vez (`Krugs + Red`)
- 🦀 **Scuttles al inicio** (0, 1 o 2) y de qué lado
- 📊 **Winrate** por ruta, por el lado en que empiezas o terminas y por scuttles
- 🗺️ **Mapa**: calor, posiciones en cualquier segundo o recorridos, tuyos o del jungla rival, victorias contra derrotas
- 🤖 **Sin tocar nada**: funciona en segundo plano y puede arrancar con Windows

![Mapa](docs/map.png)

## Cómo funciona

| Durante la partida | Después de la partida |
|---|---|
| Si llevas Castigo, graba tu **oro** de los primeros 7 minutos: cada monstruo es un salto de oro, así que los tiempos son exactos. | Cuando sales de cola, abre el **replay**, fija la cámara sobre el mapa y graba la **posición** de cada campeón (~2 min). Si entras en cola, para y lo reintenta más tarde. |

Solo usa las APIs locales oficiales del juego. No lee memoria ni tráfico de red, y no muestra nada durante tus partidas.

## Empezar

1. Descarga `JungleClearTracker.exe` desde [Releases](../../releases) (o ejecútalo desde el código: `pip install -r requirements.txt` y `python run.py`).
2. Ábrelo: el **Setup check** encuentra tu carpeta de LoL y activa la Replay API del juego con un clic.
3. Elige una carpeta de datos, pulsa **Start** y juega.

> [!NOTE]
> El exe no está firmado, así que SmartScreen o tu antivirus pueden avisar.

## A tener en cuenta

- Las reglas se ajustaron con partidas reales de Fiddlesticks; con otros campeones puede necesitar pequeños ajustes.
- Los valores que dependen del parche (posiciones de campamentos, aparición del scuttle, umbrales de oro) están en [`jct/patch.json`](jct/patch.json).
- Solo se pueden analizar las partidas grabadas a partir de ahora: los replays solo se abren en el parche en que se jugaron.
- Tus datos se quedan en la carpeta que elijas (capturas, `summaries.csv`, log).

---

<sub>Jungle Clear Tracker isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all associated properties are trademarks or registered trademarks of Riot Games, Inc. · Minimapa de Data Dragon (Riot) · [Licencia MIT](LICENSE)</sub>
