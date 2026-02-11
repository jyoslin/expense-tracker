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
    """Fetch all accounts and sort by usage frequency"""
    # 1. Get Accounts
    accounts = supabase.table('accounts').select("*").execute().data
    df_acc = pd.DataFrame(accounts)
    
    # 2. Get Transaction Counts to determine "Popularity"
    # We fetch just the account IDs from transactions to count them
    trans_data = supabase.table('transactions').select("from_account_id, to_account_id").execute().data
    
    if not trans_data:
        return df_acc # Return unsorted if no history
        
    # Count occurrences
    all_ids = [t['from_account_id'] for t in trans_data if t['from_account_id']] + \
              [t['to_account_id'] for t in trans_data if t['to_account_id']]
    
    # Create a frequency map {account_id: count}
    freq = pd.Series(all_ids).value_counts()
    
    # Map frequency to the account dataframe (default to 0 if not found)
    df_acc['usage'] = df_acc['id'].map(freq).fillna(0)
    
    # Sort by usage (highest first), then by name
    df_acc = df_acc.sort_values(by=['usage', 'name'], ascending=[False, True])
    
    return df_acc

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
    
    # STEP 1: Select Type OUTSIDE the form to trigger a refresh
    trans_type = st.radio("Transaction Type", ["Expense", "Income", "Transfer"], horizontal=True)
    
    # STEP 2: The Form
    with st.form("entry_form"):
        col1, col2 = st.columns(2)
        with col1:
            date = st.date_input("Date", datetime.today())
        with col2:
            amount = st.number_input("Amount ($)", min_value=0.01, format="%.2f")

        # Dynamic Account Selection
        from_account = None
        to_account = None
        
        # Sort accounts by most used (we will update the get_accounts function later)
        account_list = df_accounts['name'].tolist()

        if trans_type == "Expense":
            from_account = st.selectbox("Paid From", account_list)
        elif trans_type == "Income":
            to_account = st.selectbox("Deposit To", account_list)
        elif trans_type == "Transfer":
            col_a, col_b = st.columns(2)
            with col_a:
                from_account = st.selectbox("From (Source)", account_list)
            with col_b:
                to_account = st.selectbox("To (Destination)", account_list)

        desc = st.text_input("Description")
        
        submitted = st.form_submit_button("Submit Transaction")
        
        if submitted:
            # Convert names to IDs
            from_id = account_map.get(from_account)
            to_id = account_map.get(to_account)
            
            # Basic validation
            if trans_type == "Transfer" and from_id == to_id:
                st.error("Source and Destination cannot be the same!")
            else:
                add_transaction(date, amount, desc, trans_type, from_id, to_id)
                st.success("Success!")
                st.rerun()

# --- TAB 2: DASHBOARD ---
with tab2:
    st.subheader("🔍 Account Details")
    
    # 1. Select Account to View
    selected_acc_name = st.selectbox("Select Account to View", df_accounts['name'].tolist())
    selected_acc_id = account_map[selected_acc_name]
    
    # Get current balance
    curr_bal = df_accounts[df_accounts['id'] == selected_acc_id]['balance'].values[0]
    st.metric(label="Current Balance", value=f"${curr_bal:,.2f}")
    
    st.divider()
    
    # 2. Fetch Transactions for THIS account only
    # We need an "OR" filter: where account is Source OR Destination
    response = supabase.table('transactions').select("*") \
        .or_(f"from_account_id.eq.{selected_acc_id},to_account_id.eq.{selected_acc_id}") \
        .order('date', desc=True).execute()
        
    df_history = pd.DataFrame(response.data)
    
    if not df_history.empty:
        # Display the table
        # We format it to look nicer
        st.dataframe(
            df_history[['date', 'description', 'amount', 'type', 'id']], 
            hide_index=True, 
            use_container_width=True
        )
        
        # 3. DELETE/UNDO FUNCTION
        st.write("### Correction")
        col_del, col_confirm = st.columns([2, 1])
        with col_del:
            # User types the ID of the transaction to delete (Simple and safe)
            trans_id_to_del = st.number_input("Enter Transaction ID to Delete (see table above)", min_value=0, step=1)
        with col_confirm:
            st.write("") # Spacer
            st.write("") # Spacer
            if st.button("🗑️ Delete Transaction"):
                if trans_id_to_del > 0:
                    # We need to fetch the transaction first to reverse the balance
                    tx = supabase.table('transactions').select("*").eq('id', trans_id_to_del).execute().data
                    if tx:
                        tx = tx[0]
                        # Reverse the math
                        if tx['type'] == "Expense":
                            update_balance(tx['from_account_id'], tx['amount']) # Add back
                        elif tx['type'] == "Income":
                            update_balance(tx['to_account_id'], -tx['amount']) # Remove
                        elif tx['type'] == "Transfer":
                            update_balance(tx['from_account_id'], tx['amount']) # Add back to source
                            update_balance(tx['to_account_id'], -tx['amount']) # Remove from dest
                        
                        # Finally delete the row
                        supabase.table('transactions').delete().eq('id', trans_id_to_del).execute()
                        st.success("Transaction Deleted & Balance Reverted!")
                        st.rerun()
                    else:
                        st.error("Transaction ID not found.")
    else:
        st.info("No transactions found for this account.")

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
