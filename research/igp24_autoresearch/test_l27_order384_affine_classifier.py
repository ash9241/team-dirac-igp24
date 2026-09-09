from __future__ import annotations

import unittest

import l27_order384_affine_classifier as lane


def packet(q: list[int], *, shift: int = 0) -> dict:
    p = lane.affine_quotient(q, 1, shift)
    return {
        "quotientCoefficients": q,
        "candidateCoefficients": lane.quadratic_lift(p),
        "A": 1,
        "B": shift,
        "upstreamCertificate": {
            "exact": True,
            "quotientLabel": "12T30",
            "groupOrder": 384,
            "kernelOrder": 8,
            "compatibleLabels": ["24T839", "24T943", "24T1132"],
        },
    }


class L27ClassifierTests(unittest.TestCase):
    def test_resultant_and_discriminant(self) -> None:
        self.assertEqual(lane.resultant([-2, 0, 1], [0, 2]), -8)
        self.assertEqual(lane.discriminant([-2, 0, 1]), 8)

    def test_certifies_943_squareclass_branch(self) -> None:
        # q=x^12-2 has P(0)=-2 and
        # disc(q)=-12^12*2^11, hence the same nonsquare class.
        q = [-2] + [0] * 11 + [1]
        result = lane.classify_packet(packet(q))
        self.assertEqual(result["status"], "certified_24T943")
        self.assertEqual(result["compatibleLabels"], ["24T943"])

    def test_rejects_943_square_norm_branch(self) -> None:
        # q=x^12-x+1 has square constant term and nonsquare discriminant.
        q = [1, -1] + [0] * 10 + [1]
        result = lane.classify_packet(packet(q))
        self.assertEqual(result["status"], "reject_24T943")
        self.assertEqual(set(result["compatibleLabels"]), {"24T839", "24T1132"})

    def test_missing_exact_scope_fails_closed(self) -> None:
        q = [-2] + [0] * 11 + [1]
        value = packet(q)
        value["upstreamCertificate"]["exact"] = False
        result = lane.classify_packet(value)
        self.assertEqual(result["status"], "fail_closed")

    def test_candidate_mismatch_fails_closed(self) -> None:
        q = [-2] + [0] * 11 + [1]
        value = packet(q)
        value["candidateCoefficients"][0] += 1
        result = lane.classify_packet(value)
        self.assertEqual(result["status"], "fail_closed")


if __name__ == "__main__":
    unittest.main()
