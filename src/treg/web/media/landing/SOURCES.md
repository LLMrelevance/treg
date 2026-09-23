# Landing design assets

The landing layout, styles, motion modules and agent marks come from
[treg-design/landing](https://github.com/l527497426-cyber/treg-design/tree/484066dd9d37a85eba24257ac9edb401b7710357/landing),
revision `484066dd9d37a85eba24257ac9edb401b7710357`, supplied as the implementation reference.

`../../landing.html` is server-rendered; assets ship via `/media` without a separate build.
Local integration covers authentication, setup commands, tracking and support chat.
WebGL failure shows the monochrome treg mark; BFCache restores animation and layout.
See `docs/context/interface/seo.md` for landing behavior.

## Third-party files

| Files | Version | Source and license |
|---|---|---|
| `vendor/three/three.module.js`, `three.core.js` | 0.180.0 | Unmodified `three@0.180.0` npm build; MIT, `vendor/three/LICENSE` |
| `vendor/three/RoomEnvironment.js`, `FontLoader.js`, `helvetiker_regular.typeface.json` | Reference revision above | Three.js helpers and font from the design repository; retained license and font metadata |
| `vendor/lenis.min.js`, `lenis.css` | 1.3.26 | Design repository's vendored Lenis; MIT, `vendor/LICENSE` |
| `assets/*` | Reference revision above | Agent marks; `assets/LOBEHUB-LICENSE` retained. The prototype's orange treg mark is omitted. |

Compare updates against the pinned source and run landing E2E, SEO and tracking tests.
Copy library builds verbatim.
