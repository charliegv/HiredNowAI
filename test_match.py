import psycopg2
from matching import match_user
import os

def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    match_user(conn, 4)

if __name__ == "__main__":
    main()
