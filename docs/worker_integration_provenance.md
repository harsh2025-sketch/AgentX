# Worker integration provenance

Base: `cb504ed46681b08ad9b9f8248cce3ca7758363ea` (merged AX-040).

Each row is a separate two-parent integration commit. The only shared production edit was the native receipt type parameter; the canonical `NativeOutcome` variant from #145 is retained. Worker variants `T` and `NativeOutcomeT` are equivalent typing names, not behavioral differences. All other imported paths were checked against the exact source Git blob hashes.

| PR | Source head | Integration commit |
| --- | --- | --- |
| #146 | `24c00278784bd9f6852af6ea2a8e29e9bd0cf338` | `5b7f09a99267e4e3864b688b3800916b9175ea5a` |
| #147 | `01c15b8d2e9b59519f6982ac65049aad00401f43` | `330ff47ff71019762260b8ac79e7ba0122efa7e4` |
| #148 | `d148ee3dadd22057e897dfaa2268a1a2e6d43fc9` | `f3a11fca9d527d4675b3897bc68d110fd0aa8265` |
| #149 | `b825b9cbe441a19ae04ce466d204a96149d6212a` | `1b70163978259d8078ad20ad512d0d7d8fdfb160` |
| #150 | `9122411603207ddb6696b8d9f200e2f4ab1678ac` | `894d6bd02343377f7c4532766f3271291d317c41` |
| #151 | `6110b54469d9dc93ef6968f5dbccfdf68a2c81a8` | `da6abd3a7b88e1d538eb499dc082b74c825f2602` |
| #152 | `12df1d7faaffeac8915cc984bb06336cb8e9c619` | `21a52c6a620b98fae8820756a2f7ad36f719532d` |
| #153 | `a49939464b00e423fcb57236fc36db586d8b0281` | `d1a30f80d92c0d042e1f7b30b460e6c0d349c207` |
| #154 | `4c1c6b2970b6951c5ccc416abd4a8e5935463da6` | `2fcc33c596b100157fc1f01edbb16e8e3a98ecdb` |
| #155 | `fe0cf8ac3ac5e83c1b11916de50ac80aabe13dde` | `42b7111e4796c953ba78b3072ff1d67300e0fc51` |
| #156 | `ef198335056995096d9086f214b1490e208e1978` | `d58ae839ed1e7072b5f0bebd8f9b63449279a390` |

The final commit adds PR #157's formatting repair and the updated README/status ledger. Original PR histories and worker evidence remain intact. No canonical migration, architecture edge, quality gate, or worker branch is rewritten. This candidate needs combined Windows C1.01 and integration review; the individual source passes are not a combined acceptance claim.
