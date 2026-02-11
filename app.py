import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime

# --- 1. SETUP ---
# Connect to Supabase
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

st.set_page_config(page_title="My Finance", page_icon="💰")

# --- 2. HELPER FUNCTIONS ---
def get_accounts():
    """Fetch all accounts sorted by name"""
    response = supabase.table('accounts').select("*").order('name').execute()
    return pd.DataFrame(response.data)

def get_recent_transactions():
    """Fetch last 10 transactions for history view"""
    response = supabase.table('transactions').select("*").order('date', desc=True).limit(10).execute()
    return pd.DataFrame(response.data)

def update_balance(account_id, amount_change):
    """Helper to update a specific account's balance"""
    # Get current balance
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    # Update DB
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id):
    """Handle the complex logic of moving money"""
    
    # 1. Record the Transaction
    data = {
        "date": str(date),
        "amount": amount,
        "description": description,
        "type": type,
        "from_account_id": from_acc_id if type in ["Expense", "Transfer"] else None,
        "to_account_id": to_acc_id if type in ["Income", "Transfer"] else None
    }
    supabase.table('transactions').insert(data).execute()

    # 2. Update Balances based on Type
    if type == "Expense":
        # Money leaves the account (Subtract)
        update_balance(from_acc_id, -amount)
        
    elif type == "Income":
        # Money enters the account (Add)
        update_balance(to_acc_id, amount)
        
    elif type == "Transfer":
        # Take from A, Give to B
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)

def create_new_account(name, type, initial_balance):
    data = {"name": name, "type": type, "balance": initial_balance}
    supabase.table('accounts').insert(data).execute()

# --- 3. THE USER INTERFACE ---
st.title("💰 My Wealth Manager")

# Refresh data
df_accounts = get_accounts()
account_map = dict(zip(df_accounts['name'], df_accounts['id'])) # Creates a lookup dictionary e.g. {'DBS': 1, 'Citi': 2}

# Create Tabs for cleaner look
tab1, tab2, tab3 = st.tabs(["📝 Entry", "📊 Dashboard", "⚙️ Settings"])

# --- TAB 1: NEW ENTRY ---
with tab1:
    st.subheader("New Transaction")
    
    with st.form("entry_form"):
        col1, col2 = st.columns(2)
        
        with col1:
            date = st.date_input("Date", datetime.today())
            amount = st.number_input("Amount ($)", min_value=0.01, format="%.2f")
        
        with col2:
            trans_type = st.selectbox("Type", ["Expense", "Income", "Transfer"])
        
        # Dynamic Account Selection based on Type
        from_account = None
        to_account = None
        
        if trans_type == "Expense":
            from_account = st.selectbox("Paid From", df_accounts['name'])
        elif trans_type == "Income":
            to_account = st.selectbox("Deposit To", df_accounts['name'])
        elif trans_type == "Transfer":
            col_a, col_b = st.columns(2)
            with col_a:
                from_account = st.selectbox("From (Source)", df_accounts['name'])
            with col_b:
                to_account = st.selectbox("To (Destination)", df_accounts['name'])

        desc = st.text_input("Description")
        
        submitted = st.form_submit_button("Submit Transaction")
        
        if submitted:
            # Convert names to IDs
            from_id = account_map[from_account] if from_account else None
            to_id = account_map[to_account] if to_account else None
            
            add_transaction(date, amount, desc, trans_type, from_id, to_id)
            st.success("Success!")
            st.rerun()

# --- TAB 2: DASHBOARD ---
with tab2:
    st.subheader("Account Balances")
    # Show clean table of accounts
    st.dataframe(df_accounts[['name', 'type', 'balance']], hide_index=True, use_container_width=True)
    
    st.divider()
    
    st.subheader("Recent Activity")
    df_history = get_recent_transactions()
    if not df_history.empty:
        st.dataframe(df_history[['date', 'description', 'amount', 'type']], hide_index=True, use_container_width=True)
    else:
        st.info("No transactions yet.")

# --- TAB 3: SETTINGS ---
with tab3:
    st.subheader("Add New Account")
    with st.form("new_acc_form"):
        new_name = st.text_input("Account Name (e.g., UOB One)")
        new_type = st.selectbox("Type", ["Bank", "Credit Card", "Cash", "Sinking Fund", "Investment"])
        initial_bal = st.number_input("Starting Balance", value=0.0)
        
        if st.form_submit_button("Create Account"):
            create_new_account(new_name, new_type, initial_bal)
            st.success(f"Created {new_name}!")
            st.rerun()
