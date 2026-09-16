# load_peps_to_db.py
import pandas as pd
import os
import sys
import logging
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

# 🔐 Load environment variables from .env file
load_dotenv()

# 📋 CONFIGURATION
DB_CONFIG = {
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "ethiopia_monitor"),
}

TABLE_NAME = os.getenv("PEPS_TABLE", "brandwatch_data")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))

# 📁 EXCEL FILE PATHS (configurable via env or defaults)
DEFAULT_FILES = [
    "media/peps/HoPR_Candidates.xlsx",
    "media/peps/Regional_Candidates.xlsx",
    "media/peps/Executive_Members.xlsx"
]

# 🗂️ Base directory for relative paths
BASE_DIR = Path(os.getenv("BASE_DIR", Path(__file__).parent.parent))

# 🔧 LOGGING SETUP
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "logs" / "peps_import.log", mode="a")
    ]
)
logger = logging.getLogger(__name__)


def validate_db_credentials(config):
    """Ensure required DB credentials are present"""
    if not config["password"]:
        raise ValueError("DB_PASSWORD environment variable is required")
    return True


def create_db_engine(config):
    """Create SQLAlchemy engine with connection pooling"""
    db_url = (
        f"postgresql://{config['user']}:{config['password']}@"
        f"{config['host']}:{config['port']}/{config['database']}"
    )
    return create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=os.getenv("DB_ECHO", "false").lower() == "true"
    )


def get_db_columns(engine, table_name, schema="public"):
    """Fetch existing column names and types from PostgreSQL table"""
    inspector = inspect(engine)
    columns = inspector.get_columns(table_name, schema=schema)
    return {col["name"]: col["type"] for col in columns}


def normalize_column_name(col_name):
    """Normalize Excel column names to match PostgreSQL conventions"""
    return (
        str(col_name)
        .lower()
        .strip()
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("-", "_")
        .replace(".", "_")
        .replace("/", "_")
    )


def validate_dataframe(df, db_columns, file_path):
    """Validate DataFrame against database schema and log issues"""
    normalized_cols = {normalize_column_name(c): c for c in df.columns}
    df.columns = [normalize_column_name(c) for c in df.columns]
    
    missing_in_db = [c for c in df.columns if c not in db_columns]
    extra_in_db = [c for c in db_columns if c not in df.columns]
    
    if missing_in_db:
        logger.warning(f"📄 {os.path.basename(file_path)}: Dropping columns not in DB: {missing_in_db}")
        df = df[[c for c in df.columns if c in db_columns]]
    
    if extra_in_db:
        logger.info(f"📄 {os.path.basename(file_path)}: DB columns missing in Excel (will be NULL): {extra_in_db[:5]}")
    
    # Check for empty required columns if you have a list
    # required_cols = ["name", "position", "party"]
    # for col in required_cols:
    #     if col in df.columns and df[col].isna().all():
    #         logger.warning(f"⚠️ Required column '{col}' is empty in {os.path.basename(file_path)}")
    
    return df


def load_excel_to_postgres(file_path, table_name, engine, db_columns, on_conflict="skip"):
    """Load Excel file into PostgreSQL with error handling and transactions"""
    logger.info(f"📂 Processing: {os.path.basename(file_path)}")
    
    try:
        # 1. Read Excel with error handling
        df = pd.read_excel(file_path, engine="openpyxl")
        logger.info(f"  ✅ Loaded {len(df)} rows | {len(df.columns)} columns")
        
        if df.empty:
            logger.warning(f"  ⚠️ File is empty: {os.path.basename(file_path)}")
            return 0
        
        # 2. Normalize and validate
        df = validate_dataframe(df, db_columns, file_path)
        
        if df.empty:
            logger.warning(f"  ⚠️ No valid columns remaining after validation")
            return 0
        
        # 3. Clean data: handle NaN, trim strings
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip().replace("nan", None)
        
        # 4. Insert with transaction and conflict handling
        logger.info(f"  📤 Inserting into '{table_name}'...")
        inserted = 0
        
        with engine.begin() as conn:
            for chunk_start in range(0, len(df), CHUNK_SIZE):
                chunk = df.iloc[chunk_start:chunk_start + CHUNK_SIZE]
                
                if on_conflict == "skip":
                    # Simple append (may cause duplicates)
                    chunk.to_sql(
                        table_name, conn, if_exists="append", 
                        index=False, method="multi"
                    )
                elif on_conflict == "upsert":
                    # Upsert logic (requires unique constraint)
                    # This is a simplified example - adjust for your schema
                    for _, row in chunk.iterrows():
                        stmt = text(f"""
                            INSERT INTO {table_name} ({', '.join(row.index)})
                            VALUES ({', '.join([f':{col}' for col in row.index])})
                            ON CONFLICT (id) DO UPDATE SET 
                                {', '.join([f'{col}=EXCLUDED.{col}' for col in row.index if col != 'id'])}
                        """)
                        conn.execute(stmt, row.to_dict())
                
                inserted += len(chunk)
                logger.info(f"    ➡️ Inserted {inserted}/{len(df)} rows...")
        
        logger.info(f"  ✅ Successfully inserted {inserted} rows!")
        return inserted
        
    except FileNotFoundError:
        logger.error(f"  ❌ File not found: {file_path}")
        return 0
    except SQLAlchemyError as e:
        logger.error(f"  ❌ Database error: {e}")
        raise
    except Exception as e:
        logger.error(f"  ❌ Unexpected error: {e}", exc_info=True)
        raise


def main():
    """Main execution function"""
    logger.info("🚀 Starting PEPs Excel → PostgreSQL Import...")
    
    # Validate credentials
    try:
        validate_db_credentials(DB_CONFIG)
    except ValueError as e:
        logger.error(f"❌ Configuration error: {e}")
        sys.exit(1)
    
    # Create engine
    engine = create_db_engine(DB_CONFIG)
    
    # Get DB schema
    try:
        db_columns = get_db_columns(engine, TABLE_NAME)
        logger.info(f"🗄️ Connected to '{TABLE_NAME}' with {len(db_columns)} columns")
    except Exception as e:
        logger.error(f"❌ Failed to inspect table '{TABLE_NAME}': {e}")
        sys.exit(1)
    
    # Determine files to process
    files_to_process = []
    env_files = os.getenv("PEPS_FILES")
    if env_files:
        files_to_process = [Path(f.strip()) for f in env_files.split(",")]
    else:
        files_to_process = [BASE_DIR / f for f in DEFAULT_FILES]
    
    # Process each file
    total_inserted = 0
    for file_path in files_to_process:
        if not Path(file_path).exists():
            logger.warning(f"⚠️ File not found: {file_path}")
            continue
        try:
            count = load_excel_to_postgres(
                str(file_path), TABLE_NAME, engine, db_columns,
                on_conflict=os.getenv("ON_CONFLICT", "skip")
            )
            total_inserted += count
        except Exception as e:
            logger.error(f"⚠️ Failed to process {file_path}: {e}")
            continue
    
    logger.info(f"\n🏁 Import complete. Total rows inserted: {total_inserted}")
    engine.dispose()


if __name__ == "__main__":
    main()
