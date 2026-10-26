# SPDX-License-Identifier: Apache-2.0
"""G6's own integer certificates, in pure Python (owner, 2026-10-20): a factorisation re-checked by multiplying it back
and testing each factor with G6's own Miller-Rabin test, a primality answer re-checked by that test (deterministic
below 3.3e24) or by a factor or witness, and a greatest common divisor re-checked by Bezout coefficients. No SymPy
needed: the oracles are written here, and each check is fed a planted wrong answer it must refute."""
from __future__ import annotations

import pytest

from g6.algebra import integers as Z

M61 = 2**61 - 1                      # a Mersenne prime, 19 digits
M89 = 2**89 - 1                      # a Mersenne prime, 27 digits: above the deterministic bound
SPSP9 = 3825123056546413051          # a strong pseudoprime to the bases 2, 3, 5, ..., 23 (149491 * 747451 * 34233211)


@pytest.mark.parametrize("n", [2, 3, 5, 97, 7919, 104729, M61])
def test_primes_below_the_bound_are_proved_prime(n: int) -> None:
    found = Z.primality(n)
    assert found["prime"] is True and found["deterministic"] is True and found["witness"] is None


@pytest.mark.parametrize(("n", "factor"), [(1, None), (0, None), (-7, None), (561, 3), (3215031751, 151),
                                           (SPSP9, None), (M61 * (2**31 - 1), None), (1000003 * 1000033, None)])
def test_composites_are_shown_composite_with_a_factor_or_a_witness(n: int, factor) -> None:
    found = Z.primality(n)
    assert found["prime"] is False and found["deterministic"] is True
    if n >= 2:
        assert found["factor"] == factor if factor else (found["factor"] or found["witness"])
    if found["witness"]:
        assert not Z.miller_rabin(n, (found["witness"],))


def test_a_large_prime_is_only_probable() -> None:
    assert Z.DETERMINISTIC_BOUND < M89
    found = Z.primality(M89)
    assert found["prime"] is True and found["deterministic"] is False


def test_a_factorisation_is_rechecked_by_multiplying_it_back() -> None:
    passed = Z.check_factorisation(360, {2: 3, 3: 2, 5: 1})
    assert passed.status == "passed" and not passed.failures and passed.methods
    assert Z.check_factorisation(-360, {-1: 1, 2: 3, 3: 2, 5: 1}).status == "passed"
    assert Z.check_factorisation(M61 * 3, {3: 1, M61: 1}).status == "passed"


@pytest.mark.parametrize(("n", "factors", "why"), [
    (360, {2: 3, 3: 2, 7: 1}, "product"),        # the product is not n
    (360, {2: 3, 45: 1}, "45"),                  # the product is n, but 45 is not prime
    (360, {4: 1, 90: 1}, "not prime"),
    (360, {1: 1, 360: 1}, "1"),                  # 1 is not a prime factor
    (360, {2: 3, 3: 2, 5: 0, 9: 0}, "power"),    # powers start at 1
    (SPSP9, {SPSP9: 1}, "not prime"),            # a strong pseudoprime is not a prime factor
])
def test_a_planted_wrong_factorisation_is_refuted(n: int, factors: dict, why: str) -> None:
    found = Z.check_factorisation(n, factors)
    assert found.status == "refuted" and any(why in f for f in found.failures), found.failures


def test_a_factorisation_into_a_large_prime_is_undecided_not_passed() -> None:
    found = Z.check_factorisation(M89 * 2, {2: 1, M89: 1})
    assert found.status == "undecided" and "probable" in found.why


def test_a_primality_answer_is_rechecked() -> None:
    assert Z.check_is_prime(97, True).status == "passed"
    assert Z.check_is_prime(M61, True).status == "passed"
    assert Z.check_is_prime(561, False).status == "passed"
    assert Z.check_is_prime(SPSP9, False).status == "passed"
    refuted = Z.check_is_prime(561, True)
    assert refuted.status == "refuted" and "3" in refuted.failures[0]
    assert Z.check_is_prime(97, False).status == "refuted"
    assert Z.check_is_prime(SPSP9, True).status == "refuted"
    large = Z.check_is_prime(M89, True)
    assert large.status == "undecided" and large.details["sympy_deterministic"] is False


def test_sympys_own_bound_is_stated() -> None:
    assert Z.check_is_prime(97, True).details["sympy_deterministic"] is True
    assert Z.check_is_prime(2**64 + 13, False).details["sympy_deterministic"] is False


def test_a_gcd_is_rechecked_by_bezout_coefficients() -> None:
    found = Z.check_gcd([12, 18, 30], 6)
    coefficients = found.details["bezout"]
    assert found.status == "passed" and sum(c * a for c, a in zip(coefficients, [12, 18, 30], strict=True)) == 6
    assert Z.check_gcd([-12, 18], 6).status == "passed"
    assert Z.check_gcd([0, 5], 5).status == "passed"
    assert Z.check_gcd([0, 0], 0).status == "passed"


@pytest.mark.parametrize(("numbers", "g", "why"), [([12, 18, 30], 3, "combination"), ([12, 18, 30], 12, "divide"),
                                                   ([12, 18], -6, "negative"), ([0, 0], 1, "combination"),
                                                   ([0, 7], 0, "0 divides only 0")])
def test_a_planted_wrong_gcd_is_refuted(numbers: list, g: int, why: str) -> None:
    found = Z.check_gcd(numbers, g)
    assert found.status == "refuted" and any(why in f for f in found.failures), found.failures


def test_bezout_coefficients_combine_to_the_gcd() -> None:
    for numbers in ([240, 46], [17, 5, 3], [0, 9], [-8, 12, 20], [10**30 + 7, 10**20 + 3]):
        g, coefficients = Z.bezout(numbers)
        assert sum(c * a for c, a in zip(coefficients, numbers, strict=True)) == g >= 0
        assert all(a % g == 0 for a in numbers) if g else not any(numbers)
