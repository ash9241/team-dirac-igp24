import random
import time
import requests
import os
from igp24_config import api_key

# --- CONFIGURATION ---
SAIR_API_ENDPOINT = "https://api.sair.foundation/api/public/v1/competitions/igp24/submissions"
MAX_SUBMISSIONS = 1000
POLYS_PER_SUBMISSION = 1000

# Set target directory explicitly to the igp folder
TARGET_DIR = os.path.expanduser("~/Desktop/igp")

R.<x> = PolynomialRing(QQ)

def pad_coeffs(h):
    coeffs = h.list()
    while len(coeffs) < 25:
        coeffs.append(0)
    return coeffs

# --- EXPANDED HIGH-VARIANCE ALGEBRAIC STRATEGIES FOR k=1 ---

def strat_composita_asymmetric():
    """Targets rare transitive groups by shifting block sizes (e.g., 2x12 or 8x3)"""
    pairs = [(2, 12), (12, 2), (3, 8), (8, 3), (4, 6), (6, 4)]
    d1, d2 = random.choice(pairs)
    f = R([random.randint(-3, 3) for _ in range(d1)] + [1])
    g = R([random.randint(-3, 3) for _ in range(d2)] + [1])
    return pad_coeffs(f(g))

def strat_cyclic_extensions():
    """Forces solvable and cyclic substructures using x^n variations"""
    base_deg = random.choice([2, 3, 4, 6, 8, 12])
    ext_deg = 24 // base_deg
    f = R([random.randint(-2, 2) for _ in range(base_deg)] + [1])
    h = f(x^ext_deg)
    return pad_coeffs(h)

def strat_extreme_sparse():
    """Trinomials with massive gaps to trigger highly specific ramification behaviors"""
    coeffs = [0] * 25
    coeffs[24] = 1
    coeffs[0] = random.choice([-2, -1, 1, 2])
    random_power = random.randint(1, 23)
    coeffs[random_power] = random.choice([-3, -2, -1, 1, 2, 3])
    return coeffs

def strat_eisenstein_high_prime():
    """Uses higher primes to lock the local Galois groups into specialized p-adic behavior"""
    p = random.choice([3, 5, 7, 11, 13])
    coeffs = [0] * 25
    coeffs[24] = 1
    coeffs[0] = p * random.choice([1, 2, 4, 5])
    if coeffs[0] % (p**2) == 0:
        coeffs[0] += p
    for i in range(1, 24):
        if random.random() > 0.3:
            coeffs[i] = p * random.randint(-1, 1)
    return coeffs

def strat_reciprocal_skewed():
    """Palindromic structure with a randomized central core"""
    coeffs = [0] * 25
    coeffs[24] = 1
    coeffs[0] = random.choice([-1, 1])
    for i in range(1, 12):
        val = random.randint(-2, 2)
        coeffs[i] = val
        coeffs[24 - i] = val * coeffs[0]
    coeffs[12] = random.randint(-3, 3)
    return coeffs

strategies = [
    strat_composita_asymmetric,
    strat_cyclic_extensions,
    strat_extreme_sparse,
    strat_eisenstein_high_prime,
    strat_reciprocal_skewed
]

def generate_k1_candidate():
    while True:
        strat_func = random.choice(strategies)
        coeffs = strat_func()
        poly = R(coeffs)
        if poly.is_irreducible():
            return coeffs

def submit_to_sair(batch_list):
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    payload = {"payload": {"polynomials": batch_list}}
    try:
        response = requests.post(SAIR_API_ENDPOINT, headers=headers, json=payload)
        if response.status_code in [200, 201]:
            print("✅ Upload Accepted (New Group Data Streamed)!")
        elif response.status_code == 429:
            print("⏳ Rate limited. Saving progress...")
            time.sleep(2)
        else:
            print(f"⚠️ API Info: Status {response.status_code}")
    except Exception as e:
        print(f"❌ Network hiccup: {e}")

def main():
    print("🛸 IGP24 UNKNOWN GROUP HUNTER ENGINE ACTIVE...")
    print(f"🎯 Target: {MAX_SUBMISSIONS} Batches | Saving to ~/Desktop/igp\n")

    for sub_idx in range(1, MAX_SUBMISSIONS + 1):
        filename = f"k1_batch_{sub_idx}.txt"
        filepath = os.path.join(TARGET_DIR, filename)
        valid_polys = []

        print(f"--- Processing Stream {sub_idx}/{MAX_SUBMISSIONS} ---")

        with open(filepath, "w") as file:
            while len(valid_polys) < POLYS_PER_SUBMISSION:
                coeffs = generate_k1_candidate()
                csv_line = ",".join(map(str, coeffs))
                file.write(f"{csv_line}\n")
                valid_polys.append(csv_line)

                if len(valid_polys) % 250 == 0:
                    print(f"[{len(valid_polys)}/{POLYS_PER_SUBMISSION}] rare targets isolated...")

        submit_to_sair(valid_polys)

if __name__ == "__main__":
    main()
