# Streaming to Stremio on a TV

`audio4films serve` runs one HTTP server (default port 7878) that is both a Stremio addon and a
file server with Range support, so the player can seek.

## Register what to offer

- During a run: `--stremio-id tt1234567` (movie) or `--stremio-id tt1234567:SEASON:EPISODE`,
  plus `--title` and `--group` (use the same group for every episode, for autoplay).
- For an existing file in the output folder: `audio4films register FILE --stremio-id ...`.

The registry is `stremio.json` in the output folder. Only entries whose file exists are offered.

## Install the addon on the TV

1. Run `audio4films serve` and keep it running while you watch.
2. In Stremio: Add-ons → Add addon → type the printed address,
   `http://<computer-ip>:7878/manifest.json`.
3. Open the movie or episode: the source appears as "audio4films — This computer".

## Pitfalls

| Pitfall | Why | What to do |
| --- | --- | --- |
| `stremio://` install links fail | Stremio turns them into `https://`; this server speaks plain HTTP | Type the `http://` address by hand |
| Port 7000 is busy on macOS | AirPlay Receiver uses it | Keep the default 7878 |
| Source vanished after a router restart | The computer got a new IP from DHCP | Reserve a fixed IP for the computer in the router, then reinstall the addon |
| Works at home only | Plain HTTP on the LAN, no auth | Do not expose the port to the internet |
| Playback stops when you close the laptop | The server lives on the computer | Keep it awake while watching |

## Scripting the TV (Android TV, optional)

With ADB enabled on the TV you can type the address instead of using the remote:
`adb shell input text "http://<ip>:7878/manifest.json"` while the "Add addon" field is focused.
