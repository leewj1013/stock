"""Record Naver market signals that were available before today's recommendations."""

from .naver_market_signals import harvest


if __name__ == "__main__":
    print({"recommendation_signals": harvest()})
