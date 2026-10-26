# SPDX-License-Identifier: Apache-2.0
"""G6's own integer certificates, in pure Python: none of it is SymPy's code.

* :func:`primality`: trial division by the primes below 1000, then the strong (Miller-Rabin) test to the 13 prime
  bases from 2 to 41, which decides every number below :data:`DETERMINISTIC_BOUND` (about 3.3e24; Sorenson and
  Webster, 2015). A factor or a witness found proves a number composite at any size; above the bound, passing the test
  makes a number only a probable prime;
* :func:`check_factorisation`: the factors, each to its power, multiplied back to the number, and each factor's
  primality tested as above;
* :func:`check_is_prime`: SymPy's answer against the test above. SymPy's ``isprime`` is deterministic below 2^64
  (:data:`SYMPY_DETERMINISTIC_BOUND`), and above it rests on the BPSW test, which has no known counterexample but is
  not a proof; a large prime is therefore re-checked as undecided, never as passed;
* :func:`check_gcd`: a greatest common divisor shown to be one by dividing every number and by Bezout coefficients
  (from G6's own extended Euclidean algorithm) that combine the numbers to it, so every common divisor divides it.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from g6.algebra.schema import Recheck

#: Every n below this is decided by the strong test to the bases 2, 3, 5, ..., 41 (Sorenson and Webster, 2015).
DETERMINISTIC_BOUND = 3_317_044_064_679_887_385_961_981
#: SymPy's ``isprime`` says its answer is definitive below this.
SYMPY_DETERMINISTIC_BOUND = 2**64
BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)
_SMALL = [p for p in range(2, 1000) if all(p % q for q in range(2, int(p ** 0.5) + 1))]
_PRIMALITY = ("G6's own primality test: trial division below 1000, then the strong test to the bases 2 to 41, which "
              "decides every number below 3.3e24")


def miller_rabin(n: int, bases: Sequence[int] = BASES) -> bool:
    """Whether odd ``n > 2`` passes the strong test to every base: ``False`` proves it composite."""
    d, s = n - 1, 0
    while d % 2 == 0:
        d, s = d // 2, s + 1
    for a in bases:
        if a % n == 0:
            continue
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def primality(n: int) -> dict[str, Any]:
    """Whether ``n`` is prime, whether that is decided (``deterministic``), and the factor or witness that shows a
    composite."""
    found: dict[str, Any] = {"prime": False, "deterministic": True, "factor": None, "witness": None}
    if n < 2:
        return found
    for p in _SMALL:
        if n == p:
            return found | {"prime": True}
        if n % p == 0:
            return found | {"factor": p}
    for a in BASES:
        if not miller_rabin(n, (a,)):
            return found | {"witness": a}
    return found | {"prime": True, "deterministic": n < DETERMINISTIC_BOUND}


def _named(p: int) -> str:
    text = str(p)
    return text if len(text) <= 40 else f"{text[:18]}...{text[-18:]} ({len(text)} digits)"


def check_factorisation(n: int, factors: Mapping[int, int]) -> Recheck:
    """``factors`` (prime to power, with ``-1`` for a negative ``n``) multiplied back to ``n``, each factor tested."""
    failures: list[str] = []
    probable: list[str] = []
    product = 1
    for p, k in factors.items():
        if not isinstance(k, int) or k < 1:
            failures.append(f"the power of {_named(p)} is {k}: powers start at 1")
            continue
        product *= p ** k
        if p == -1:
            if n >= 0 or k != 1:
                failures.append("-1 is a factor only of a negative number, once")
            continue
        if p < 2:
            failures.append(f"{p} is not a prime factor: primes start at 2")
            continue
        test = primality(p)
        if not test["prime"]:
            shown = (f"it has the factor {test['factor']}" if test["factor"] else
                     f"the base {test['witness']} is a witness")
            failures.append(f"{_named(p)} is not prime: {shown}")
        elif not test["deterministic"]:
            probable.append(_named(p))
    if product != n:
        failures.insert(0, f"the product of the factors is {_named(product)}, not {_named(n)}")
    methods = ("the factors, each to its power, multiplied back exactly", _PRIMALITY + ", on each factor")
    if failures:
        return Recheck("refuted", methods, tuple(failures[:5]))
    if probable:
        return Recheck("undecided", methods, why=f"the product is exact, but {', '.join(probable[:3])} "
                                                 "is only a probable prime: above 3.3e24 G6's test is not a proof",
                       details={"probable_primes": probable})
    return Recheck("passed", methods)


def check_is_prime(n: int, answer: bool) -> Recheck:
    """SymPy's ``isprime`` answer for ``n`` against G6's own test."""
    test = primality(n)
    details = {"sympy_deterministic": n < SYMPY_DETERMINISTIC_BOUND, "g6_deterministic": test["deterministic"],
               "factor": test["factor"], "witness": test["witness"]}
    if test["factor"]:
        shown = f"it has the factor {test['factor']}"
    elif test["witness"]:
        shown = f"the base {test['witness']} is a witness to its being composite"
    else:
        shown = "it is below 2"
    if answer and not test["prime"]:
        return Recheck("refuted", (_PRIMALITY,), (f"{_named(n)}: SymPy says it is prime, but {shown}",),
                       details=details)
    if not answer and test["prime"]:
        if test["deterministic"]:
            return Recheck("refuted", (_PRIMALITY,), (f"{_named(n)}: SymPy says it is not prime, but G6's test, which "
                                                      "decides numbers of this size, finds it prime",), details=details)
        return Recheck("undecided", (_PRIMALITY,), why=f"SymPy says {_named(n)} is not prime, but G6's test finds no "
                                                       "factor or witness, and above 3.3e24 that decides nothing",
                       details=details)
    if not test["prime"]:
        return Recheck("passed", (f"{_PRIMALITY}; {shown}, which proves it composite at any size",), details=details)
    if test["deterministic"]:
        return Recheck("passed", (f"{_PRIMALITY}; it passes, and at this size that decides it",), details=details)
    return Recheck("undecided", (_PRIMALITY,), why=f"{_named(n)} is above 3.3e24, where neither SymPy's test (BPSW, "
                                                  "above 2^64) nor G6's proves a number prime", details=details)


def bezout(numbers: Sequence[int]) -> tuple[int, list[int]]:
    """The greatest common divisor of ``numbers`` (never negative) and coefficients that combine them to it."""
    g, coefficients = 0, [0] * len(numbers)
    for i, a in enumerate(numbers):
        # extended Euclid on (g, a): x*g + y*a = d
        old_r, r, old_x, x, old_y, y = g, a, 1, 0, 0, 1
        while r:
            q = old_r // r
            old_r, r = r, old_r - q * r
            old_x, x = x, old_x - q * x
            old_y, y = y, old_y - q * y
        if old_r < 0:
            old_r, old_x, old_y = -old_r, -old_x, -old_y
        coefficients = [c * old_x for c in coefficients]
        coefficients[i] = old_y
        g = old_r
    return g, coefficients


def check_gcd(numbers: Sequence[int], g: int) -> Recheck:
    """``g`` shown to be the greatest common divisor of ``numbers``: it divides each, and Bezout coefficients combine
    the numbers to it, so every common divisor divides it."""
    failures: list[str] = []
    found, coefficients = bezout(numbers)
    if g < 0:
        failures.append(f"a greatest common divisor is never negative, and {g} is")
    elif g == 0:
        if any(numbers):
            failures.append("0 divides only 0, and the numbers are not all 0")
    else:
        failures += [f"{_named(g)} does not divide {_named(a)}" for a in numbers if a % g][:3]
    if not failures and g != found:
        failures.append(f"{_named(g)} is not an integer combination of the numbers: G6's Bezout coefficients combine "
                        f"them to {_named(found)}, so a larger common divisor exists"
                        if found else f"{_named(g)} is not an integer combination of the numbers, which are all 0")
    methods = ("divides every number", "Bezout coefficients from G6's own extended Euclidean algorithm combine the "
                                       "numbers to it, so every common divisor divides it")
    if failures:
        return Recheck("refuted", methods, tuple(failures), details={"bezout": coefficients, "gcd_by_g6": found})
    return Recheck("passed", methods, details={"bezout": coefficients})


__all__ = ["BASES", "DETERMINISTIC_BOUND", "SYMPY_DETERMINISTIC_BOUND", "bezout", "check_factorisation", "check_gcd",
           "check_is_prime", "miller_rabin", "primality"]
