import math


def is_prime(n: int) -> bool:
    """Return True if n is a prime number, False otherwise."""
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False

    # Check divisibility by numbers of the form 6k±1 up to sqrt(n)
    limit = int(math.isqrt(n))
    for i in range(5, limit + 1, 6):
        if n % i == 0 or n % (i + 2) == 0:
            return False

    return True


# Example usage
if __name__ == "__main__":
    test_numbers = [0, 1, 2, 3, 4, 5, 17, 18, 19, 20, 97, 100]
    for num in test_numbers:
        print(f"{num}: {'prime' if is_prime(num) else 'not prime'}")