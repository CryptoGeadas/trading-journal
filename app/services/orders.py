"""Rules for an order: the fills of one exchange order shown as one row."""


def mixed_fee_text(db, fill_ids: list[str]) -> str:
    """The fee of an order whose fills paid in different coins (e.g. USDT and BNB), one amount per
    coin: "0.001 BNB; 0.5 USDT". Amounts in different coins are never added up."""
    parts = db.execute(
        f"""SELECT fee_currency, SUM(fee) FROM trades WHERE id IN ({",".join("?" * len(fill_ids))})
            GROUP BY fee_currency ORDER BY fee_currency""", fill_ids).fetchall()
    return "; ".join(f"{amount:g} {currency or '?'}" for currency, amount in parts)
