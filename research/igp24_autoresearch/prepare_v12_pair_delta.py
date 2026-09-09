#!/usr/bin/env python3
"""Freeze and audit the exact v12 unordered-pair signature delta.

This is a light, offline preparer.  It does not import Sage or GAP, touch the
network, write the ledger, or submit.  It advances the completed v11 boundary
by the verified scoreable rows in the nine pinned shared-result receipts,
audits their exact candidate/Frobenius/manifest chains, and emits one command
for a signature-aware, one-worker v12 census.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as v11


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V11 = ROOT / "data/autopilot_pair_delta_20260722_v11"
V12 = ROOT / "data/autopilot_pair_delta_20260722_v12"
GROUP_INPUT = V12 / "group_input.jsonl"
GROUP_SUMMARY = V12 / "group_input_summary.json"
CENSUS_INPUT = V12 / "census_input.jsonl"
INVENTORY = V12 / "exact_source_inventory.json"
PLAN = V12 / "provenance_plan.json"

EXPECTED_V11_DELTA = {("24T5971", 24), ("24T15253", 0)}

STAGER = ROOT / "stage_frobenius_gold.py"

PINNED_SHA256 = {
    "v11Preparer": "f2dd432a90afa3d2c8584add46d3b3aef25903315a806c30cff8b7ece2acd393",
    "v11GroupInput": "5e5d36479a8da006993e0cc788774607a3760185d03a5ce5dc5a3c71b3a82910",
    "v11CensusInput": "d48536ac3197ed570bc61c509339506da8983a11259904b915a0f58dcb2ffb00",
    "v11Inventory": "333024576811ef7d78f1cbb2b2daf6bde489820d66de7838e72043896b0d1f4c",
    "v11Plan": "e25f07d0747bb3b71494ab03fbbbec74ddf9275109e68158430c3586cb36f72e",
    "v11CompletedCensus": "12f5c2ace30abdd288f9ea76ff670782a0fcb2a50574a14d8ffad1a818fafd7c",
    "stager": "222ba8c9f3c517fb4b9a39225d82d32bf372207e698d9f027b0c21bcbf5fb204",
    "receiptV1": "3667448a1689c458d1e2a6a53139b7d1b318e4db42bd8d4d77bb93b8552fc7e9",
    "manifestV1": "66d70aa890ba12fdfef57f9ef30e5a8050bc53bf4dd842dde67d60523f0829bb",
    "candidatesV1": "4437f7d54b3105724b8859b2c20e772b473a624bf018332db57dcefa57afc2f3",
    "frobeniusV1": "04aa60b7ea1c063b685959149e3b8c80b83ee0be14ac2deade252fadf9e4241b",
    "receiptV2": "742b36542dbd76269296c42c651cb964306bcee0e5baec03b496f8b201cdec38",
    "manifestV2": "86c631d8729ad3ab46c037417721a70d4bd6ebe2633fd9793a5356e52c13f2ea",
    "candidatesV2": "8a7acd578f4cf6f62ab48782087a1715d030a4e8eb73a4338a09722b51900843",
    "frobeniusV2": "ddad765099cec2b3758ac24aa77b1861ea43f27b9849fbf63fbfd751853498b0",
    "receiptV3": "29526f38a030482b85cc20655aef15269635678cb876d1a3aa130d726b9c76c4",
    "manifestV3": "aa613a1cc3d22fcc8bd01d85b6185154af072c585b8ee3035f994aa2ce5d0e7e",
    "candidatesV3": "b30c1393f0d95b4ed2d6fdc5eeb3a2b7f23e9b713a3d3e5c50477264dfa2321d",
    "frobeniusV3": "a7178ea79738245a1155219b440f6225fb190ad566fcd21031cb8eb67ffd174a",
    "receiptTop10990": "4772f1abc51377d99757cb4460c21f74c38cdd3c65617904f018d063fc64ac36",
    "manifestTop10990": "1d3015d0234784474527545046a877e5affee67180f172e46a21a64caafd0814",
    "candidatesTop10990": "391d6209e94d9b0d1e35ff3ad9c88024096e17d50d4ffb605a5ddd32459884a1",
    "frobeniusTop10990": "bacb61113cf22ae9c80926886919d24b5488b15ac953e1c5b48bb8f7cc1489b6",
    "receiptTop15250": "299c700fafb82b5e979f68d81cc0515ca9333c14a3cca05eb82e1e60e66afc55",
    "manifestTop15250": "30e58fe477a770697e708e5143c997142751919b1f0c20bcfcd8b3e62bdb41b9",
    "candidatesTop15250": "9c567f05c46273270bbc1480b264bca445ec2f432360ea661667c90fe67ca169",
    "frobeniusTop15250": "fdb75ac486878244268bea77d0692a07edda503dc7a71bec74e67e24aa58ec60",
    "receiptTop16947": "6bb3532a60e4b66526029bc205a423e181190fbecd96e1e9a6b5ad9a9561e27d",
    "manifestTop16947": "076feb21dcbc327dc78e05f73e0c03db61c62424da7cd3d98c3b48e8bdf3de96",
    "candidatesTop16947": "8df2f80903ce40ec9c681a540df416d8163e6c5d6d876cfe5606058c856bd047",
    "frobeniusTop16947": "a60f56c3e2d79faf4ed50a111dca7df218342dcfb369d3bde9ad4353dde441e1",
    "receiptTop8354": "bf3169f4c35557527c581a24e4ca3e5983591323d08e114a733ad8b974db6a5e",
    "manifestTop8354": "59f1987ee9e512025f9b0bae55bacc5f306b074afe78ee9f7e68b760b05466a1",
    "candidatesTop8354": "147bf46d9c9b5db7236333ec508607049847921a4eedc40e3286931048750c53",
    "frobeniusTop8354": "43e5080b467043e330acd106524fcb8abcab2327aebae34bbe2f8289bd94b36b",
    "receiptTop16965": "cf9b508b6dcb68e0466914fd91de63a95f1e92bdd763499ec14aa5634ff22984",
    "manifestTop16965": "05480bcf2ab9ee02ff8e433cd0dafcf2fd381f8d5bf6d69d93f589d42ad47a36",
    "candidatesTop16965": "7faef8d93c56fbdafffe71f0eb503968a8c8ec1b7df90c05f848528b2c6fa1f7",
    "frobeniusTop16965": "af42a323808b856600e2be02480d851bbc104f74fc1eefebe534ae4fc8849aed",
    "receiptAllExact": "10eaea5c823d5fe103c277a6928e364606a2fc7adb9a5b1f99677421fd6958cb",
    "manifestAllExact": "838aada1259fbb3767395c4f6b59e8e37c5ae0752ba5a005a527280f2ff96f48",
    "allExactCertificate": "d4a17816978ced79904435f4ff52f621f079a1e71feb90dbb8b0d3aa7309bf82",
    "allExactSummary": "e7d2194e4e2ac0e850002912a019036d22705e9e37a1260698adca4bf47f64db",
    "allExactStager": "df564c068d4bf81687dfcdaf0fb5427d400463db5ed0a248d7d2cddef6a3fe39",
}


def spec(label: str, r: int, coefficient_hash: str) -> dict:
    return {"label": label, "r": r, "coefficientSha256": coefficient_hash}


BATCHES = [
    {
        "name": "sharedV1",
        "submissionId": "sub_da0fed27ae954acca4948bf5bdbb0d79",
        "description": "Team Dirac exact candidate batch 20260722-40",
        "receipt": ROOT / "receipts/sub_da0fed27ae954acca4948bf5bdbb0d79.json",
        "receiptPin": "receiptV1",
        "manifest": ROOT / "outbox/autopilot_exact_frobenius_shared_20260722_v1.txt",
        "manifestPin": "manifestV1",
        "candidates": ROOT / "data/agent_index24_ambiguous_pair_multi_packets.jsonl",
        "candidatesPin": "candidatesV1",
        "frobenius": ROOT / "data/agent_index24_ambiguous_pair_multi_frobenius_certificate.json",
        "frobeniusPin": "frobeniusV1",
        "allowUnresolved": True,
        "expectedSummary": {"contradiction": 0, "resolved": 14, "rows": 15, "unresolved": 1},
        "sources": [
            spec("24T2016", 0, "e3629d99c6c26b0370094e8279860f40f7dd06b9c90859a2a87b31002368bd67"),
            spec("24T2053", 4, "1e1f56dcbf9fd4b87a1cad620f0471d1231d160d9ba3bf86365e6628856efc4c"),
            spec("24T3853", 0, "ac2baf5e741917265cfc7c5b542845869b56abaaeda72efa5aaa13319cb2f050"),
            spec("24T6822", 8, "63186b618ae46c2c256ff4cf0fb4a000060c996dfd1413564a00617080acc744"),
            spec("24T9112", 8, "80d884803e6b3933d7b38cd42b2ca6973fb13fc9e9b8848e5bb60ed3490f143c"),
            spec("24T9458", 8, "c2b3a1b3e78ca9a8c0467408262eed4e5884f54bfd5e963bcea3b581daa1570a"),
            spec("24T14956", 8, "d3cea212ece1ce3e22968367fcbc0056a7af2d7fe044795cbb3e9b25ebc83208"),
            spec("24T15367", 8, "dc9db9c92620401a1de788065d913763f73b4931d3eb86e9d8ff9d05ec2788a4"),
            spec("24T15367", 12, "3b50f82b15e48e353cd3ee998a98c90e779b39847ce5f6987b4b5c05c2658c38"),
            spec("24T15831", 12, "f25e1bd6671fe8af332fdcd17bef7001ce142c40674dbf9d497afa8e35038365"),
            spec("24T20027", 16, "f0278c7a7c3a2f02e82fdf298455b74a3d10b5891d8b11b323ea66185c7d2734"),
        ],
    },
    {
        "name": "sharedV2",
        "submissionId": "sub_407df6d1ba6d48d6a116bf29eb2b1c81",
        "description": "Team Dirac exact candidate batch 20260722-41",
        "receipt": ROOT / "receipts/sub_407df6d1ba6d48d6a116bf29eb2b1c81.json",
        "receiptPin": "receiptV2",
        "manifest": ROOT / "outbox/autopilot_exact_frobenius_shared_20260722_v2.txt",
        "manifestPin": "manifestV2",
        "candidates": ROOT / "data/pair_sum_gold_routes2_candidates.jsonl",
        "candidatesPin": "candidatesV2",
        "frobenius": ROOT / "data/pair_sum_gold_routes2_frobenius_certificate.json",
        "frobeniusPin": "frobeniusV2",
        "allowUnresolved": False,
        "expectedSummary": {"contradiction": 0, "resolved": 2, "rows": 2, "unresolved": 0},
        "sources": [
            spec("24T9049", 16, "b2d3b5ebf2dea52c652b1cb6fb2a466b5c2819754a219b829707c1047bc6e19a"),
        ],
    },
    {
        "name": "sharedV3",
        "submissionId": "sub_18ebcdf912d043629d4f52460722b439",
        "description": "Team Dirac exact candidate batch 20260722-42",
        "receipt": ROOT / "receipts/sub_18ebcdf912d043629d4f52460722b439.json",
        "receiptPin": "receiptV3",
        "manifest": ROOT / "outbox/autopilot_exact_frobenius_shared_20260722_v3.txt",
        "manifestPin": "manifestV3",
        "candidates": ROOT / "data/autopilot_pair_sum_multi_ranked_v3.jsonl",
        "candidatesPin": "candidatesV3",
        "frobenius": ROOT / "data/autopilot_pair_sum_multi_ranked_v3_frobenius.json",
        "frobeniusPin": "frobeniusV3",
        "allowUnresolved": True,
        "expectedSummary": {"contradiction": 0, "resolved": 14, "rows": 15, "unresolved": 1},
        "sources": [
            spec("24T3438", 4, "da0d90425fb78dc824af557005b65f3deaf03252014a9f98b70aa31062d48d3f"),
            spec("24T3583", 0, "d87fc396caa560037119051cd5c7d8d40178e30346dd69b5260935f5b0c14887"),
            spec("24T3647", 4, "2243e47df4410bf5a078a4432e1e21b6cf8cdc858a8c63815b09d7361f22f044"),
            spec("24T3836", 4, "9a7ba0b821cf6f053be5a93ba5182d07317e8ddadc5cf6940201ee1eb7858197"),
            spec("24T3919", 0, "49e618f3bd98a2d7d3e18a71c1cc81dca1a2ffde300babd39dd0f570a3c0d3a1"),
            spec("24T4083", 4, "e7044c5ebf0d526ad20df925fad3b121ec89b7dccd17759e194c65daa0a26565"),
            spec("24T8525", 0, "6a6dddcc667f729ad5f2c1b997b73ab805eb6d69f4919300f381474c7c260a96"),
            spec("24T10688", 16, "c5aade139051765476cca377ff3d4afb3fd4a6508641d083a670917f6e205ecb"),
            spec("24T11143", 16, "ebcb299a59f3e98d61d05f7cd04e434bc156b766fdaafc935faae25ad5597134"),
            spec("24T11749", 8, "677197dbd3ca94ef9038116bec72c76aba537f93c0bc63877867d17362059123"),
            spec("24T18918", 16, "7a8a421b6a132abf3980d1cde62f2f735c999954aea545acd1c01582d655bdbc"),
            spec("24T18952", 16, "36f9dcacbee91a91f81f771624a5906923f5aee635ded5b50bc5e241212a5252"),
        ],
    },
    {
        "name": "sharedTop10990",
        "submissionId": "sub_3da98b7713b94f3a86d41e126574cc97",
        "description": "Team Dirac exact candidate batch 20260722-43",
        "receipt": ROOT / "receipts/sub_3da98b7713b94f3a86d41e126574cc97.json",
        "receiptPin": "receiptTop10990",
        "manifest": ROOT / "outbox/autopilot_exact_shared_top_10990_20260722.txt",
        "manifestPin": "manifestTop10990",
        "candidates": ROOT / "data/autopilot_pair_delta_20260722_v3/wave_c/source_10482_r20_candidates.json",
        "candidatesPin": "candidatesTop10990",
        "frobenius": ROOT / "data/autopilot_pair_delta_20260722_v3/wave_c/source_10482_r20_frobenius.json",
        "frobeniusPin": "frobeniusTop10990",
        "allowUnresolved": False,
        "expectedSummary": {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0},
        "sources": [
            spec("24T10990", 20, "b82c9afeda3d7fb009ccb3d90181f6a3637e09639c55934ad2c3b3bc9cc2e76b"),
        ],
    },
    {
        "name": "sharedTop15250",
        "submissionId": "sub_d036a30ecdbe431f99366ac5d2a43786",
        "description": "Team Dirac exact candidate batch 20260722-44",
        "receipt": ROOT / "receipts/sub_d036a30ecdbe431f99366ac5d2a43786.json",
        "receiptPin": "receiptTop15250",
        "manifest": ROOT / "outbox/autopilot_exact_shared_top_15250_20260722.txt",
        "manifestPin": "manifestTop15250",
        "candidates": ROOT / "data/autopilot_pair_delta_20260722_v4/ambiguous_15130_to_15250/source_15130_r8_candidates.json",
        "candidatesPin": "candidatesTop15250",
        "frobenius": ROOT / "data/autopilot_pair_delta_20260722_v4/ambiguous_15130_to_15250/source_15130_r8_frobenius.json",
        "frobeniusPin": "frobeniusTop15250",
        "allowUnresolved": False,
        "expectedSummary": {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0},
        "sources": [
            spec("24T15250", 8, "20e5dbdd7f2abc8c0ed45964b065bb36acc6121e04d84c33466b8da556971435"),
        ],
    },
    {
        "name": "sharedTop16947",
        "submissionId": "sub_1306e5812c224d41aa3c07975157a88e",
        "description": "Team Dirac exact candidate batch 20260722-45",
        "receipt": ROOT / "receipts/sub_1306e5812c224d41aa3c07975157a88e.json",
        "receiptPin": "receiptTop16947",
        "manifest": ROOT / "outbox/autopilot_exact_shared_top_16947_20260722.txt",
        "manifestPin": "manifestTop16947",
        "candidates": ROOT / "data/autopilot_pair_delta_20260722_v4/ambiguous_17398_to_16947/source_17398_r8_candidates.json",
        "candidatesPin": "candidatesTop16947",
        "frobenius": ROOT / "data/autopilot_pair_delta_20260722_v4/ambiguous_17398_to_16947/source_17398_r8_frobenius.json",
        "frobeniusPin": "frobeniusTop16947",
        "allowUnresolved": False,
        "expectedSummary": {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0},
        "sources": [
            spec("24T16947", 8, "3a289db8a4cc5cdc59c488afbe3725d747d239a381623b78b2ae7db415dc7c6e"),
        ],
    },
    {
        "name": "sharedTop8354",
        "submissionId": "sub_91b1902709d9431eb73fa51ef409a622",
        "description": "Team Dirac exact candidate batch 20260722-46",
        "receipt": ROOT / "receipts/sub_91b1902709d9431eb73fa51ef409a622.json",
        "receiptPin": "receiptTop8354",
        "manifest": ROOT / "outbox/autopilot_exact_shared_top_8354_20260722.txt",
        "manifestPin": "manifestTop8354",
        "candidates": ROOT / "data/autopilot_pair_delta_20260722_v10/ambiguous_8535_multi_candidates.jsonl",
        "candidatesPin": "candidatesTop8354",
        "frobenius": ROOT / "data/autopilot_pair_delta_20260722_v10/ambiguous_8535_frobenius.json",
        "frobeniusPin": "frobeniusTop8354",
        "allowUnresolved": False,
        "expectedSummary": {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0},
        "sources": [
            spec("24T8354", 4, "f5b01f6430574737e6e941617c0b6b2a82da3403dc334b038e5ed300b01db381"),
        ],
    },
    {
        "name": "sharedTop16965",
        "submissionId": "sub_68e3059ad02e43e6988352fc402c7167",
        "description": "Team Dirac exact candidate batch 20260722-47",
        "receipt": ROOT / "receipts/sub_68e3059ad02e43e6988352fc402c7167.json",
        "receiptPin": "receiptTop16965",
        "manifest": ROOT / "outbox/autopilot_exact_shared_top_16965_20260722.txt",
        "manifestPin": "manifestTop16965",
        "candidates": ROOT / "data/autopilot_pair_delta_20260722_v4/ambiguous_17499_to_16965/source_17499_r8_candidates.json",
        "candidatesPin": "candidatesTop16965",
        "frobenius": ROOT / "data/autopilot_pair_delta_20260722_v4/ambiguous_17499_to_16965/source_17499_r8_frobenius.json",
        "frobeniusPin": "frobeniusTop16965",
        "allowUnresolved": False,
        "expectedSummary": {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0},
        "sources": [
            spec("24T16965", 8, "afcdb88259ddb90fcfeddcd4d2029fbe5bc0f16b974ec96478bf1026157468ec"),
        ],
    },
    {
        "name": "allExactShared",
        "chainKind": "allExactSealedStage",
        "submissionId": "sub_25603f23e0a24a30b4828f883d6046d6",
        "description": "Team Dirac exact candidate batch 20260722-48",
        "receipt": ROOT / "receipts/sub_25603f23e0a24a30b4828f883d6046d6.json",
        "receiptPin": "receiptAllExact",
        "manifest": ROOT / "outbox/all_exact_frobenius_unowned_20260722.txt",
        "manifestPin": "manifestAllExact",
        "stageCertificate": ROOT / "data/all_exact_frobenius_unowned_20260722_certificate.json",
        "stageCertificatePin": "allExactCertificate",
        "stageSummary": ROOT / "data/all_exact_frobenius_unowned_20260722_summary.json",
        "stageSummaryPin": "allExactSummary",
        "stageScript": ROOT / "stage_all_exact_frobenius_unowned.py",
        "stageScriptPin": "allExactStager",
        "sources": [
            spec("24T6640", 8, "6579b9ee62942f3feac7dea1087d0ccbd7896a2187a91b85342f42bd4b9cdd64"),
            spec("24T12811", 8, "dea002276429f47343ca436c0ae2abedddd50426614a56d00cc72c29f4df915e"),
            spec("24T17507", 24, "f2c73b91f00105b905a73cdd8cf19bd882763a0064718d7ff8743148a5e07a4d"),
            spec("24T3782", 4, "89f806c09af2ac982445a37006264229845f1f3998051791abac86ea496362ad"),
            spec("24T17244", 16, "46f3d8461d8ddebf658c3b86e94bef1806ec2cf481c1ee4886c754bb85fc7fd6"),
            spec("24T3782", 0, "8b01a8db620d6c4ae3647eb1e8e8ed7ff28e2a6b21f4a480b1aec2bb9cd9b260"),
            spec("24T3899", 4, "d954d2d91251486d66acaa609f0dc6e64407400e4d98cdd5083895a8b8d0bc95"),
            spec("24T4164", 8, "0004102e9ab8cd29b96187e3e624fb4f8a54fcd73a32582be34259251ce21f04"),
            spec("24T15043", 8, "d0598e9f492c5b4bff6e7f7ffc7ccfe1fb63b6ff8ea6e325658d263ceaec19ba"),
            spec("24T15717", 8, "fdeffa33cd7996608e0081909a077737c35467976ca2ff3dcf6ecf47c4454131"),
            spec("24T12811", 0, "37f2a2ebf3e88d55d206a9a581ece9947b6d8e7ee1358f875c4693f5b40c86e4"),
            spec("24T2083", 8, "565eca8be838d28cdd8d3ae47b75c34df413c8a8458063ce8d948914ffc2c0bc"),
            spec("24T4095", 0, "88914ee141a595d526b4bdb2a9c78f0c596cabfcec8a6096ce95cf000f87f5c8"),
            spec("24T15756", 8, "302baba8cd341bb354f8aad64b6286150766c04d5166188fe83a865933a32c1f"),
            spec("24T1584", 0, "1439f5ce145d15c5d95aabaa407949e13ebc7b4aa42601d8b5fe8203f0438663"),
            spec("24T1827", 4, "07a2810977787b201299d4dd43f2b33e88df7c44baab4e05876d5b4134754e98"),
            spec("24T13571", 0, "fb2a57b97231accbecd261c39e213f22df91e0bd4802bed6500234831d787596"),
            spec("24T18861", 10, "49685c812db0b1db81c56b7e87fea343c1e4d051c222364db7b8636af814c0d1"),
            spec("24T13196", 4, "573b3ed700dc97bdf0963a89ae0153c2347d836236524ac59af4f2987b226b79"),
        ],
    },
]

SOURCE_PAIRS = {
    (source["label"], int(source["r"]))
    for batch in BATCHES
    for source in batch["sources"]
}
if len(SOURCE_PAIRS) != 48:
    raise RuntimeError("v12 source specification is not 48 distinct pairs")

PRIOR_MAPS = [
    "data/autopilot_pair_delta_20260721_v2/pair_prior_sources.jsonl",
    *[
        f"data/autopilot_pair_delta_20260721_v2/missing_pair_shard{index}.jsonl"
        for index in range(6)
    ],
    "data/autopilot_pair_delta_20260722_v3/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v4/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v5/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v6/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v8/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v9/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v10/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v11/missing_pair_all.jsonl",
]


def require_pinned(path: Path, key: str) -> None:
    actual = v11.sha256_path(path)
    if actual != PINNED_SHA256[key]:
        raise ValueError(f"pinned {key} hash mismatch: {actual}")


def pair_string(pair: tuple[str, int]) -> str:
    return f"{pair[0]}:{pair[1]}"


def pair_sort(pair: tuple[str, int]) -> tuple[int, int]:
    return int(pair[0][3:]), pair[1]


def source_key(row: dict) -> tuple[str, int]:
    return str(row["sourceSubmissionId"]), int(row["sourcePolynomialIndex"])


def validate_v11_checkpoint() -> tuple[list[dict], list[dict]]:
    pinned = {
        ROOT / "prepare_v11_pair_delta.py": "v11Preparer",
        V11 / "group_input.jsonl": "v11GroupInput",
        V11 / "census_input.jsonl": "v11CensusInput",
        V11 / "exact_source_inventory.json": "v11Inventory",
        V11 / "provenance_plan.json": "v11Plan",
        V11 / "missing_pair_all.jsonl": "v11CompletedCensus",
    }
    for path, key in pinned.items():
        require_pinned(path, key)

    group_rows = v11.read_jsonl(V11 / "group_input.jsonl")
    census_input_rows = v11.read_jsonl(V11 / "census_input.jsonl")
    census_rows = v11.read_jsonl(V11 / "missing_pair_all.jsonl")
    inventory = v11.read_json(V11 / "exact_source_inventory.json")
    plan = v11.read_json(V11 / "provenance_plan.json")
    expected_strings = {pair_string(pair) for pair in EXPECTED_V11_DELTA}

    if (
        inventory.get("schemaVersion") != "v11-exact-source-inventory-v1"
        or inventory.get("status") != "certified"
        or set(inventory.get("deltaPairs") or []) != expected_strings
        or int(inventory.get("deltaPairCount", -1)) != 2
        or int(inventory.get("verifiedPairCount", -1)) != 2
    ):
        raise ValueError("pinned v11 inventory envelope mismatch")
    if (
        plan.get("schemaVersion") != "v11-pair-delta-provenance-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or set((plan.get("delta") or {}).get("pairs") or []) != expected_strings
        or int((plan.get("execution") or {}).get("selectedWorkerRows", -1)) != 2
    ):
        raise ValueError("pinned v11 provenance-plan envelope mismatch")

    expected_artifacts = {
        "groupInput": V11 / "group_input.jsonl",
        "censusInput": V11 / "census_input.jsonl",
        "exactSourceInventory": V11 / "exact_source_inventory.json",
        "preparer": ROOT / "prepare_v11_pair_delta.py",
    }
    for name, path in expected_artifacts.items():
        item = (plan.get("artifacts") or {}).get(name) or {}
        if (
            v11.rooted_artifact(item.get("path")) != path.resolve()
            or item.get("sha256") != v11.sha256_path(path)
        ):
            raise ValueError(f"v11 plan does not pin {name}")

    completed_pairs = {
        (str(row.get("sourceLabel")), int(signature))
        for row in census_rows
        for signature in row.get("sourceR") or []
    }
    if (
        len(census_rows) != 2
        or completed_pairs != EXPECTED_V11_DELTA
        or v11.source_pairs(census_input_rows) != EXPECTED_V11_DELTA
        or any(row.get("status") != "certified" for row in census_rows)
        or any(not v11.certificate_is_exact(row) for row in census_rows)
    ):
        raise ValueError("completed v11 census is not the exact 2/2 certificate set")
    return group_rows, census_rows


def candidate_rows_by_hash(path: Path, context: str) -> dict[str, list[tuple[dict, dict]]]:
    rows_by_hash: dict[str, list[tuple[dict, dict]]] = {}
    for row in v11.read_jsonl(path):
        orbit = row.get("orbitCertificate") or {}
        if (
            row.get("status") != "certified_multi"
            or ("workerExitCode" in row and int(row["workerExitCode"]) != 0)
            or orbit.get("actualDegrees") != orbit.get("expectedDegrees")
            or any(int(value) != 1 for value in orbit.get("exponents") or [])
            or int(row.get("networkCalls", 0)) != 0
            or int(row.get("submissionCalls", 0)) != 0
            or int(row.get("ledgerWrites", 0)) != 0
        ):
            raise ValueError(f"{context} has a non-exact candidate row")
        for candidate in row.get("candidates") or []:
            digest = str(candidate.get("coefficientSha256"))
            if v11.sha256_bytes(str(candidate.get("coefficientLine")).encode()) != digest:
                raise ValueError(f"{context} candidate coefficient hash mismatch")
            rows_by_hash.setdefault(digest, []).append((row, candidate))
    return rows_by_hash


def validate_all_exact_chain(batch: dict, lines: list[str]) -> list[dict]:
    certificate = v11.read_json(batch["stageCertificate"])
    summary = v11.read_json(batch["stageSummary"])
    manifest_item = certificate.get("manifest") or {}
    stage_item = certificate.get("stageScript") or {}
    checks = certificate.get("checks") or {}
    selected = certificate.get("selected") or []
    if (
        certificate.get("method") != "all-exact-frobenius-unowned-sealed-stage-v1"
        or int(certificate.get("networkCalls", -1)) != 0
        or int(certificate.get("submissionCalls", -1)) != 0
        or not checks
        or not all(value is True for value in checks.values())
        or v11.rooted_artifact(manifest_item.get("path")) != batch["manifest"].resolve()
        or manifest_item.get("sha256") != PINNED_SHA256[batch["manifestPin"]]
        or int(manifest_item.get("polynomials", -1)) != len(batch["sources"])
        or v11.rooted_artifact(stage_item.get("path")) != batch["stageScript"].resolve()
        or stage_item.get("sha256") != PINNED_SHA256[batch["stageScriptPin"]]
        or len(selected) != len(batch["sources"])
    ):
        raise ValueError("all-exact sealed-stage certificate envelope mismatch")
    if (
        summary.get("status") != "sealed_not_submitted"
        or v11.rooted_artifact(summary.get("certificate"))
        != batch["stageCertificate"].resolve()
        or summary.get("certificateSha256")
        != PINNED_SHA256[batch["stageCertificatePin"]]
        or v11.rooted_artifact(summary.get("manifest")) != batch["manifest"].resolve()
        or summary.get("manifestSha256") != PINNED_SHA256[batch["manifestPin"]]
        or int(summary.get("polynomials", -1)) != len(batch["sources"])
    ):
        raise ValueError("all-exact sealed-stage summary mismatch")

    # The sealed stage ran after the five per-certificate singleton receipts.
    # Their intact manifests were verifier anchors that excluded those hashes
    # and pairs from the combined manifest before the 19-row batch was sealed.
    anchor_batches = BATCHES[3:8]
    receipt_entries = {
        str(row.get("submissionId")): row
        for row in (certificate.get("receiptExclusion") or {}).get("receipts") or []
    }
    unsynced = set(
        (certificate.get("receiptExclusion") or {}).get("unsyncedSubmissionIds") or []
    )
    for anchor_batch in anchor_batches:
        anchor = receipt_entries.get(anchor_batch["submissionId"]) or {}
        evidence = anchor.get("manifestEvidence") or []
        expected_path = str(anchor_batch["manifest"].relative_to(ROOT))
        expected_hash = PINNED_SHA256[anchor_batch["manifestPin"]]
        intact = [
            item
            for item in evidence
            if item.get("path") == expected_path
            and item.get("recordedSha256") == expected_hash
            and item.get("status") == "intact"
            and int(item.get("hashesUsed", -1)) == 1
        ]
        if (
            int(anchor.get("expectedPolynomials", -1)) != 1
            or int(anchor.get("exactMappedHashes", -1)) != 1
            or int(anchor.get("receiptHashesUsed", -1)) != 1
            or int(anchor.get("receiptPairsUsed", -1)) != 1
            or len(intact) != 1
            or anchor_batch["submissionId"] not in unsynced
        ):
            raise ValueError(f"missing all-exact receipt anchor: {anchor_batch['name']}")

    provenance = []
    for polynomial_index, (line, source, sealed) in enumerate(
        zip(lines, batch["sources"], selected, strict=True)
    ):
        digest = source["coefficientSha256"]
        pair = f"{source['label']}/r{source['r']}"
        target = sealed.get("target") or {}
        if (
            sealed.get("coefficientSha256") != digest
            or sealed.get("pair") != pair
            or target.get("label") != source["label"]
            or int(target.get("r", -1)) != int(source["r"])
            or v11.sha256_bytes(line.encode()) != digest
        ):
            raise ValueError(f"all-exact selected row mismatch: {polynomial_index}")
        proof_records = []
        proofs = sealed.get("proofs") or []
        if not proofs:
            raise ValueError(f"all-exact selected row has no proof: {polynomial_index}")
        for proof in proofs:
            artifact = v11.rooted_artifact(proof.get("artifact"))
            input_path = v11.rooted_artifact(proof.get("input"))
            if (
                v11.sha256_path(artifact) != proof.get("artifactSha256")
                or v11.sha256_path(input_path) != proof.get("inputSha256")
            ):
                raise ValueError("all-exact selected proof artifact hash mismatch")
            frobenius = v11.read_json(artifact)
            if (
                frobenius.get("method")
                != "exact-unramified-frobenius-cycle-type-exclusion-v1"
                or v11.rooted_artifact(frobenius.get("input")) != input_path.resolve()
                or frobenius.get("inputSha256") != proof.get("inputSha256")
            ):
                raise ValueError("all-exact selected proof certificate mismatch")
            source_tuple = (
                str(proof["sourceSubmissionId"]),
                int(proof["sourcePolynomialIndex"]),
            )
            assignment_matches = [
                (row, assignment)
                for row in frobenius.get("rows") or []
                if row.get("status") == "resolved" and source_key(row) == source_tuple
                for assignment in row.get("assignments") or []
                if assignment.get("coefficientSha256") == digest
                and assignment.get("targetLabel") == source["label"]
                and int(assignment.get("targetR", -1)) == int(source["r"])
            ]
            candidate_matches = candidate_rows_by_hash(
                input_path, f"all-exact proof {artifact.name}"
            ).get(digest) or []
            exact_candidates = [
                (row, candidate)
                for row, candidate in candidate_matches
                if source_key(row) == source_tuple
                and candidate.get("coefficientLine") == line
            ]
            if len(assignment_matches) != 1 or len(exact_candidates) != 1:
                raise ValueError("all-exact selected proof does not close uniquely")
            assignment = assignment_matches[0][1]
            candidate = exact_candidates[0][1]
            if int(assignment.get("factorIndex", -1)) != int(candidate.get("factorIndex", -2)):
                raise ValueError("all-exact proof factor assignment mismatch")
            proof_records.append(
                {
                    "frobeniusCertificate": v11.relative_artifact(artifact),
                    "candidates": v11.relative_artifact(input_path),
                    "assignmentPointer": proof.get("assignmentPointer"),
                }
            )
        provenance.append(
            {
                "submissionId": batch["submissionId"],
                "polynomialIndex": polynomial_index,
                "pair": pair_string((source["label"], int(source["r"]))),
                "coefficientSha256": digest,
                "sealedStageProofs": proof_records,
            }
        )
    return provenance


def validate_batch_chain(batch: dict) -> list[dict]:
    pin_pairs = [("receipt", "receiptPin"), ("manifest", "manifestPin")]
    if batch.get("chainKind") == "allExactSealedStage":
        pin_pairs.extend(
            [
                ("stageCertificate", "stageCertificatePin"),
                ("stageSummary", "stageSummaryPin"),
                ("stageScript", "stageScriptPin"),
            ]
        )
    else:
        pin_pairs.extend(
            [("candidates", "candidatesPin"), ("frobenius", "frobeniusPin")]
        )
    for path_name, pin_name in pin_pairs:
        require_pinned(batch[path_name], batch[pin_name])

    receipt = v11.read_json(batch["receipt"])
    response = receipt.get("response") or {}
    if (
        receipt.get("commit") is not True
        or receipt.get("description") != batch["description"]
        or int(receipt.get("polynomials", -1)) != len(batch["sources"])
        or response.get("submissionId") != batch["submissionId"]
        or response.get("description") != batch["description"]
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
        or v11.rooted_artifact(receipt.get("manifest")) != batch["manifest"].resolve()
        or receipt.get("manifestHash") != PINNED_SHA256[batch["manifestPin"]]
    ):
        raise ValueError(f"{batch['name']} receipt envelope mismatch")

    lines = batch["manifest"].read_text(encoding="utf-8").splitlines()
    if len(lines) != len(batch["sources"]):
        raise ValueError(f"{batch['name']} manifest row count mismatch")
    for line, source in zip(lines, batch["sources"], strict=True):
        v11.validated_coefficient_line(line, source["coefficientSha256"])

    if batch.get("chainKind") == "allExactSealedStage":
        return validate_all_exact_chain(batch, lines)

    candidates = v11.read_jsonl(batch["candidates"])
    certificate = v11.read_json(batch["frobenius"])
    certificate_rows = certificate.get("rows") or []
    if (
        certificate.get("method")
        != "exact-unramified-frobenius-cycle-type-exclusion-v1"
        or v11.rooted_artifact(certificate.get("input"))
        != batch["candidates"].resolve()
        or certificate.get("inputSha256")
        != PINNED_SHA256[batch["candidatesPin"]]
        or certificate.get("selectedInputRowsSha256")
        != PINNED_SHA256[batch["candidatesPin"]]
        or certificate.get("summary") != batch["expectedSummary"]
        or len(certificate_rows) != int(batch["expectedSummary"]["rows"])
    ):
        raise ValueError(f"{batch['name']} Frobenius certificate envelope mismatch")
    actual_status = {
        status: sum(row.get("status") == status for row in certificate_rows)
        for status in ("resolved", "unresolved", "contradiction")
    }
    if actual_status != {
        "resolved": int(batch["expectedSummary"]["resolved"]),
        "unresolved": int(batch["expectedSummary"]["unresolved"]),
        "contradiction": int(batch["expectedSummary"]["contradiction"]),
    }:
        raise ValueError(f"{batch['name']} Frobenius row-status census mismatch")
    if actual_status["unresolved"] and not batch["allowUnresolved"]:
        raise ValueError(f"{batch['name']} unexpectedly requires --allow-unresolved")

    candidates_by_hash = candidate_rows_by_hash(batch["candidates"], batch["name"])

    assignments_by_hash: dict[str, list[tuple[dict, dict]]] = {}
    for row in certificate_rows:
        if row.get("status") != "resolved":
            continue
        for assignment in row.get("assignments") or []:
            assignments_by_hash.setdefault(
                str(assignment.get("coefficientSha256")), []
            ).append((row, assignment))

    provenance = []
    for polynomial_index, (line, source) in enumerate(
        zip(lines, batch["sources"], strict=True)
    ):
        digest = source["coefficientSha256"]
        candidate_matches = candidates_by_hash.get(digest) or []
        assignment_matches = assignments_by_hash.get(digest) or []
        if len(candidate_matches) != 1 or len(assignment_matches) != 1:
            raise ValueError(
                f"{batch['name']} manifest row lacks one unique exact chain: {polynomial_index}"
            )
        candidate_row, candidate = candidate_matches[0]
        certificate_row, assignment = assignment_matches[0]
        if (
            source_key(candidate_row) != source_key(certificate_row)
            or candidate.get("coefficientLine") != line
            or int(candidate.get("factorIndex", -1))
            != int(assignment.get("factorIndex", -2))
            or int(candidate.get("targetR", -1)) != int(source["r"])
            or assignment.get("targetLabel") != source["label"]
            or int(assignment.get("targetR", -1)) != int(source["r"])
        ):
            raise ValueError(f"{batch['name']} exact chain mismatch: {polynomial_index}")
        provenance.append(
            {
                "submissionId": batch["submissionId"],
                "polynomialIndex": polynomial_index,
                "pair": pair_string((source["label"], int(source["r"]))),
                "coefficientSha256": digest,
                "factorIndex": int(candidate["factorIndex"]),
                "constructionSource": {
                    "submissionId": candidate_row["sourceSubmissionId"],
                    "polynomialIndex": int(candidate_row["sourcePolynomialIndex"]),
                    "label": candidate_row["sourceLabel"],
                    "r": int(candidate_row["sourceR"]),
                },
            }
        )
    return provenance


def prior_action(source_label: str) -> dict | None:
    matches = []
    for relative in PRIOR_MAPS:
        for row in v11.read_jsonl(ROOT / relative):
            if row.get("sourceLabel") == source_label:
                matches.append(row)
    if not matches:
        return None
    signatures = {
        (
            int(row.get("length24OrbitCount", -1)),
            json.dumps(row.get("targetCounts") or {}, sort_keys=True),
        )
        for row in matches
    }
    if len(signatures) != 1:
        raise ValueError(f"conflicting prior unordered-pair actions for {source_label}")
    return matches[-1]


def construction_inventory(batch: dict) -> dict:
    if batch.get("chainKind") == "allExactSealedStage":
        return {
            "sealedStageCertificate": v11.relative_artifact(batch["stageCertificate"]),
            "sealedStageSummary": v11.relative_artifact(batch["stageSummary"]),
            "sealedStageScript": v11.relative_artifact(batch["stageScript"]),
            "selectedManifestRows": len(batch["sources"]),
            "receiptExclusionAnchors": [item["name"] for item in BATCHES[3:8]],
        }
    return {
        "candidates": v11.relative_artifact(batch["candidates"]),
        "frobeniusCertificate": v11.relative_artifact(batch["frobenius"]),
        "stager": v11.relative_artifact(STAGER),
        "stagingFlags": {
            "allowUnresolved": bool(batch["allowUnresolved"]),
            "includeShared": True,
        },
        "selectedManifestRows": len(batch["sources"]),
    }


def main() -> int:
    v11_rows, _v11_census = validate_v11_checkpoint()
    require_pinned(STAGER, "stager")
    construction_rows = [
        row for batch in BATCHES for row in validate_batch_chain(batch)
    ]
    if len(construction_rows) != 48:
        raise ValueError("exact construction-chain count is not 48")

    v11_pairs = v11.source_pairs(v11_rows)
    if SOURCE_PAIRS & v11_pairs:
        raise ValueError("v12 delta collides with the frozen v11 source boundary")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    accepted_records = [
        (
            str(submission_id),
            int(polynomial_index),
            str(label),
            int(r),
            str(coefficient_hash),
        )
        for submission_id, polynomial_index, label, r, coefficient_hash
        in connection.execute(
            "SELECT v.submission_id,v.polynomial_index,v.label,v.r,p.coefficient_hash "
            "FROM verifications v JOIN polynomials p "
            "USING(submission_id,polynomial_index) "
            "WHERE v.status='accepted' AND v.scoreable=1"
        )
    ]
    accepted = {(label, r) for _, _, label, r, _ in accepted_records}
    accepted_delta = accepted - v11_pairs
    if accepted_delta != SOURCE_PAIRS:
        raise ValueError(
            "accepted ledger boundary moved beyond the pinned v12 checkpoint: "
            f"{sorted(accepted_delta ^ SOURCE_PAIRS, key=pair_sort)}"
        )
    if SOURCE_PAIRS & baseline:
        raise ValueError("v12 delta intersects the immutable baseline")

    expected_rows = {
        (
            batch["submissionId"],
            polynomial_index,
            source["label"],
            int(source["r"]),
            source["coefficientSha256"],
        )
        for batch in BATCHES
        for polynomial_index, source in enumerate(batch["sources"])
    }
    actual_rows = {
        row for row in accepted_records if (row[2], row[3]) not in v11_pairs
    }
    if actual_rows != expected_rows:
        raise ValueError("post-v11 accepted verification rows are not exactly the pinned 48")

    verifications = []
    for batch in BATCHES:
        for polynomial_index, source in enumerate(batch["sources"]):
            verification = connection.execute(
                "SELECT status,label,r,scoreable,in_baseline,scoring_status "
                "FROM verifications WHERE submission_id=? AND polynomial_index=?",
                (batch["submissionId"], polynomial_index),
            ).fetchone()
            expected = (
                "accepted",
                source["label"],
                int(source["r"]),
                1,
                0,
                "scoreable",
            )
            if verification != expected:
                raise ValueError(
                    f"v12 source is not the exact accepted verification: "
                    f"{batch['submissionId']}:{polynomial_index}"
                )
            verifications.append(
                {
                    "submissionId": batch["submissionId"],
                    "polynomialIndex": polynomial_index,
                    "pair": pair_string((source["label"], int(source["r"]))),
                    "coefficientSha256": source["coefficientSha256"],
                    "status": "accepted",
                    "scoreable": True,
                    "inBaseline": False,
                }
            )

    target_records = [
        (str(label), int(r), int(team_count), bool(discovered), str(generated_at))
        for label, r, team_count, discovered, generated_at in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    ]
    connection.close()

    target_labels = {label for label, *_rest in target_records}
    if len(target_records) != 165_836 or len(target_labels) != 25_000:
        raise ValueError(
            "authoritative target refresh cardinality mismatch: "
            f"{len(target_labels)} labels/{len(target_records)} pairs"
        )

    owned_pairs = v11_pairs | accepted
    owned_by_label: dict[str, set[int]] = {}
    for label, r in owned_pairs:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label: dict[str, set[int]] = {}
    target_snapshot = {}
    generated_at_values = []
    for label, r, team_count, discovered, generated_at in target_records:
        generated_at_values.append(generated_at)
        pair = (label, r)
        if pair in SOURCE_PAIRS:
            target_snapshot[pair] = {
                "pair": pair_string(pair),
                "teamCount": team_count,
                "discovered": discovered,
                "generatedAt": generated_at,
            }
        if team_count == 0 and pair not in baseline and pair not in owned_pairs:
            gold_by_label.setdefault(label, set()).add(r)
    if set(target_snapshot) != SOURCE_PAIRS:
        raise ValueError("one or more v12 sources are absent from the target snapshot")

    labels = sorted(
        set(owned_by_label) | set(gold_by_label), key=lambda label: int(label[3:])
    )
    v12_rows = [
        {
            "goldR": sorted(gold_by_label.get(label, set())),
            "isGoldTarget": label in gold_by_label,
            "isOwnedSource": label in owned_by_label,
            "label": label,
            "sourceR": sorted(owned_by_label.get(label, set())),
            "t": int(label[3:]),
        }
        for label in labels
    ]
    v12_pairs = v11.source_pairs(v12_rows)
    if v12_pairs - v11_pairs != SOURCE_PAIRS or v11_pairs - v12_pairs:
        raise ValueError("v12 group input does not advance v11 by exactly 48 pairs")
    v11.atomic_text(GROUP_INPUT, v11.canonical_jsonl(v12_rows))

    group_summary = {
        "schemaVersion": "v12-frozen-group-input-summary-v1",
        "status": "certified",
        "base": v11.relative_artifact(V11 / "group_input.jsonl"),
        "groupInput": v11.relative_artifact(GROUP_INPUT),
        "rows": len(v12_rows),
        "baseOwnedPairs": len(v11_pairs),
        "frozenOwnedPairs": len(v12_pairs),
        "baseGoldPairs": len(v11.gold_pairs(v11_rows)),
        "frozenGoldPairs": len(v11.gold_pairs(v12_rows)),
        "targetLabels": len(target_labels),
        "targetRows": len(target_records),
        "targetGeneratedAtMin": min(generated_at_values),
        "targetGeneratedAtMax": max(generated_at_values),
        "exactDeltaPairs": [pair_string(pair) for pair in sorted(SOURCE_PAIRS, key=pair_sort)],
        "derivation": "refreshed target cache plus accepted ledger, audited against completed v11",
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    v11.atomic_json(GROUP_SUMMARY, group_summary)

    census_rows = []
    for row in v12_rows:
        selected = sorted(r for label, r in SOURCE_PAIRS if row.get("label") == label)
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if v11.source_pairs(census_rows) != SOURCE_PAIRS:
        raise ValueError("v12 census input does not isolate exactly the 48-pair delta")
    selected_worker_rows = sum(bool(row["isOwnedSource"]) for row in census_rows)
    if selected_worker_rows != 45:
        raise ValueError(f"unexpected v12 selected label-row count: {selected_worker_rows}")
    v11.atomic_text(CENSUS_INPUT, v11.canonical_jsonl(census_rows))

    v11_signatures: dict[str, set[int]] = {}
    for label, r in v11_pairs:
        v11_signatures.setdefault(label, set()).add(r)
    selected_preflight = {
        pair for pair in SOURCE_PAIRS if pair[1] not in v11_signatures.get(pair[0], set())
    }
    if selected_preflight != SOURCE_PAIRS:
        raise ValueError("signature-aware worker preflight selected the wrong v12 delta")

    route_sources = []
    structural_total = 0
    live_total = 0
    for label, r in sorted(SOURCE_PAIRS, key=pair_sort):
        action = prior_action(label)
        if action is None:
            structural = 276 // 24
            live = structural
            entry = {
                "sourcePair": pair_string((label, r)),
                "knownLength24Orbits": None,
                "structuralRouteCeiling": structural,
                "currentLiveRouteCeiling": live,
                "reason": (
                    "Label absent from every unordered-pair map through v11; "
                    "276 unordered pairs permit at most floor(276/24)=11 "
                    "degree-24 orbits."
                ),
            }
        else:
            targets = action.get("targets") or []
            if len(targets) != int(action.get("length24OrbitCount", -1)):
                raise ValueError(f"prior action target cardinality mismatch for {label}")
            external = [row for row in targets if row.get("targetLabel") != label]
            structural = len(external)
            live = sum(
                bool(gold_by_label.get(str(row.get("targetLabel")), set()))
                for row in external
            )
            entry = {
                "sourcePair": pair_string((label, r)),
                "knownLength24Orbits": int(action["length24OrbitCount"]),
                "knownExternalLength24Orbits": structural,
                "knownExternalTargetCounts": {
                    target_label: sum(
                        row.get("targetLabel") == target_label for row in external
                    )
                    for target_label in sorted(
                        {str(row.get("targetLabel")) for row in external}
                    )
                },
                "structuralRouteCeiling": structural,
                "currentLiveRouteCeiling": live,
                "reason": (
                    "Abstract action is already frozen; the new source signature "
                    "still requires exact class profiling."
                ),
            }
        structural_total += structural
        live_total += live
        route_sources.append(entry)
    if structural_total != 331:
        raise ValueError(f"unexpected v12 structural route ceiling: {structural_total}")

    inventory = {
        "schemaVersion": "v12-exact-source-inventory-v1",
        "status": "certified",
        "base": {
            "v11GroupInput": v11.relative_artifact(V11 / "group_input.jsonl"),
            "v11CensusInput": v11.relative_artifact(V11 / "census_input.jsonl"),
            "v11ExactSourceInventory": v11.relative_artifact(V11 / "exact_source_inventory.json"),
            "v11ProvenancePlan": v11.relative_artifact(V11 / "provenance_plan.json"),
            "v11CompletedCensus": v11.relative_artifact(V11 / "missing_pair_all.jsonl"),
        },
        "v12GroupInput": v11.relative_artifact(GROUP_INPUT),
        "deltaPairs": [pair_string(pair) for pair in sorted(SOURCE_PAIRS, key=pair_sort)],
        "deltaPairCount": 48,
        "deltaLabelCount": 45,
        "verifiedPairCount": 48,
        "verifications": verifications,
        "verifierAnchor": {
            "method": "read-only-ledger-accepted-scoreable-post-v11-v1",
            "acceptedRows": 48,
            "distinctPairs": 48,
            "unaccountedRows": 0,
            "rows": verifications,
        },
        "receipts": {
            batch["name"]: v11.relative_artifact(batch["receipt"]) for batch in BATCHES
        },
        "manifests": {
            batch["name"]: v11.relative_artifact(batch["manifest"]) for batch in BATCHES
        },
        "constructionProvenance": {
            batch["name"]: construction_inventory(batch) for batch in BATCHES
        },
        "selectedConstructionRows": construction_rows,
        "collisionCensus": {
            "baselinePairCollisions": 0,
            "duplicateDeltaPairs": 0,
            "priorFrozenSignatureCollisions": 0,
            "unaccountedAcceptedPairs": 0,
            "unaccountedAcceptedRows": 0,
            "receiptFailedPolynomials": 0,
            "receiptRejectedPolynomials": 0,
        },
        "targetSnapshot": [
            target_snapshot[pair] for pair in sorted(SOURCE_PAIRS, key=pair_sort)
        ],
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    v11.atomic_json(INVENTORY, inventory)

    command = [
        "caffeinate",
        "-i",
        "sage",
        "-python",
        "agent_index24_missing_pair_census.sage.py",
        "--input",
        str(CENSUS_INPUT.relative_to(ROOT)),
    ]
    for prior_map in PRIOR_MAPS:
        command.extend(["--prior-map", prior_map])
    command.extend(
        [
            "--signature-aware",
            "--prior-input",
            "data/autopilot_pair_delta_20260722_v11/group_input.jsonl",
            "--output",
            "data/autopilot_pair_delta_20260722_v12/missing_pair_all.jsonl",
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    plan = {
        "schemaVersion": "v12-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker",
        "base": {
            "v11GroupInput": v11.relative_artifact(V11 / "group_input.jsonl"),
            "v11ProvenancePlan": v11.relative_artifact(V11 / "provenance_plan.json"),
            "v11CompletedCensus": v11.relative_artifact(V11 / "missing_pair_all.jsonl"),
        },
        "artifacts": {
            "groupInput": v11.relative_artifact(GROUP_INPUT),
            "groupInputSummary": v11.relative_artifact(GROUP_SUMMARY),
            "censusInput": v11.relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": v11.relative_artifact(INVENTORY),
            "preparer": v11.relative_artifact(Path(__file__).resolve()),
            "priorMaps": [v11.relative_artifact(ROOT / path) for path in PRIOR_MAPS],
            "worker": v11.relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"),
        },
        "delta": {
            "selectedLabels": 45,
            "selectedSignatures": 48,
            "distinctPairs": 48,
            "pairs": [pair_string(pair) for pair in sorted(SOURCE_PAIRS, key=pair_sort)],
            "acceptedLedgerDeltaPairs": 48,
            "acceptedLedgerDeltaRows": 48,
            "signatureBaseline": "completed v11 frozen group input and 2/2 census",
        },
        "routeExpectation": {
            "unorderedPairSetSizePerSource": 276,
            "sources": route_sources,
            "totalStructuralRouteCeiling": structural_total,
            "totalCurrentLiveRouteCeiling": live_total,
            "status": "rigorous_upper_bounds_not_yet_signature_censused",
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "selectedWorkerRows": selected_worker_rows,
            "selectedWorkerSignatures": 48,
            "plannedCommand": command,
            "requiresRootHeavyWorkerClearance": True,
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    v11.atomic_json(PLAN, plan)
    print(
        json.dumps(
            {
                "status": "ready_for_one_heavy_worker",
                "deltaPairs": 48,
                "selectedWorkerRows": selected_worker_rows,
                "selectedWorkerSignatures": 48,
                "structuralRouteCeiling": structural_total,
                "currentLiveRouteCeiling": live_total,
                "plannedCommand": command,
                "plan": str(PLAN.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
