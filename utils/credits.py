import psycopg2
from psycopg2.extras import DictCursor


def init_credit_balance(
    conn,
    user_id: int,
    initial_credits: int,
    reason: str = "trial_grant",
    reference_id: str | None = None,
):
    """
    Initialise credit balance for a user.
    Safe to call multiple times (idempotent).
    """

    with conn.cursor(cursor_factory=DictCursor) as cur:
        try:
            cur.execute("BEGIN")

            # Ensure balance row exists
            cur.execute(
                """
                INSERT INTO credit_balance (user_id)
                VALUES (%s)
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id,)
            )

            # Lock balance row
            cur.execute(
                """
                SELECT available_credits
                FROM credit_balance
                WHERE user_id = %s
                FOR UPDATE
                """,
                (user_id,)
            )

            balance = cur.fetchone()
            if balance is None:
                raise Exception("Credit balance row missing after insert")

            # Prevent double init
            if balance["available_credits"] > 0:
                cur.execute("ROLLBACK")
                return False

            # Ledger entry
            cur.execute(
                """
                INSERT INTO credit_ledger (
                    user_id,
                    change_amount,
                    reason,
                    reference_id
                )
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, initial_credits, reason, reference_id)
            )

            # Update balance
            cur.execute(
                """
                UPDATE credit_balance
                SET
                    available_credits = %s,
                    lifetime_granted = %s,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (initial_credits, initial_credits, user_id)
            )

            cur.execute("COMMIT")
            return True

        except Exception:
            cur.execute("ROLLBACK")
            raise
