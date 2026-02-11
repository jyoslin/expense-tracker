import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime

# 1. SETUP DATABASE CONNECTION
# We get these secrets from the Streamlit settings (we will set this up next)
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# 2. HELPER FUNCTIONS (The "Tools")
def get_accounts():
    """Fetch all accounts from Supabase"""
    response = supabase.table('accounts').select("*").execute()
    return pd.DataFrame(response.data)

def add_transaction(date, amount, description, type, account_id):
    """Send a new transaction to Supabase"""
    data = {
        "date": str(date),
        "amount": amount,
        "description": description,
        "type": type,
        "from_account_id": account_id 
        # We will add 'to_account_id' logic later for transfers
    }
    supabase.table('transactions').insert(data).execute()
    
    # Update account balance (Simple version: just subtract/add)
    # Note: In a real app, we'd calculate balance from transactions, 
    # but for now, let's update the account directly to see instant results.
    current_balance = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    
    if type == "Expense":
        new_balance = float(current_balance) - float(amount)
    else:
        new_balance = float(current_balance) + float(amount)
        
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

# 3. THE APP INTERFACE (What you see on screen)
st.title("💰 My Personal Finance")

# --- SECTION A: VIEW ACCOUNTS ---
st.subheader("My Accounts")
df_accounts = get_accounts()
# Show a clean table
st.dataframe(df_accounts[['name', 'type', 'balance']])

# --- SECTION B: ADD TRANSACTION ---
st.subheader("Add New Transaction")

with st.form("transaction_form"):
    col1, col2 = st.columns(2)
    
    with col1:
        date = st.date_input("Date", datetime.today())
        amount = st.number_input("Amount ($)", min_value=0.01, format="%.2f")
    
    with col2:
        # Create a dropdown list of accounts using the data we fetched
        account_names = df_accounts['name'].tolist()
        selected_account_name = st.selectbox("Account", account_names)
        # Find the ID of the selected account
        selected_account_id = int(df_accounts[df_accounts['name'] == selected_account_name]['id'].values[0])
        
        trans_type = st.selectbox("Type", ["Expense", "Income"])
    
    desc = st.text_input("Description (e.g., Lunch, Salary)")
    
    submitted = st.form_submit_button("Save Transaction")
    
    if submitted:
        add_transaction(date, amount, desc, trans_type, selected_account_id)
        st.success("Transaction Saved!")
        st.rerun() # Refresh the page to show new balance
