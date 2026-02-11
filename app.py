import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- 1. SETUP ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

# --- 2. HELPER FUNCTIONS ---
def get_accounts():
    """Fetch accounts sorted by usage"""
    accounts = supabase.table('accounts').select("*").execute().data
    df_acc = pd.DataFrame(accounts)
    if df_acc.empty: return pd.DataFrame(columns=['id', 'name', 'type', 'balance'])
    
    # Sort: Put 'Bank' and 'Credit Card' first for easy access
    df_acc['sort_key'] = df_acc['type'].map({'Bank': 1, 'Credit Card': 2, 'Sinking Fund': 3, 'Custodial': 4}).fillna(5)
    return df_acc.sort_values(by=['sort_key', 'name'])

def run_scheduled_transactions():
    """Auto-run due transactions"""
    today = datetime.today().date()
    tasks = supabase.table('schedule').select("*").lte('next_run_date', str(today)).execute().data
    count = 0
    if tasks:
        for task in tasks:
            # Execute
            add_transaction(task['next_run_date'], task['amount'], f"🔄 {task['description']}", 
                          task['type'], task['from_account_id'], task['to_account_id'])
            
            # Reschedule or Delete
            if task['frequency'] == 'Monthly':
                next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
            else:
                supabase.table('schedule').delete().eq("id", task['id']).execute()
            count += 1
    return count

def update_balance(account_id, amount_change):
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id):
    # 1. Record Transaction
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id
    }).execute()

    # 2. Update Balances
    if type == "Expense":
        update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]:
        update_balance(to_acc_id, amount)
    elif type == "Transfer":
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)
    elif type == "Custodial In": 
        # Special Logic: Money Enters Bank (Asset UP), but Liability also UP (Custodial Account goes Negative)
        # We treat this as a Transfer from Custodial(Liability) -> Bank
        update_balance(from_acc_id, -amount) # Liability becomes more negative (You owe more)
        update_balance(to_acc_id, amount)    # Bank balance increases

def get_projection(target_date, current_accounts):
    """Calculate future balance based on schedule"""
    # Get all scheduled items between now and target date
    today = datetime.today().date()
    # We need to loop because 'Monthly' items might happen multiple times
    
    # Create a copy of current balances to simulate on
    proj_balances = current_accounts.set_index('id')['balance'].to_dict()
    
    # Fetch all active schedules
    schedules = supabase.table('schedule').select("*").execute().data
    
    simulated_log = []
    
    for sch in schedules:
        # Start from the next run date
        run_date = datetime.strptime(sch['next_run_date'], '%Y-%m-%d').date()
        
        while run_date <= target_date:
            # Simulate the transaction
            amt = float(sch['amount'])
            
            if sch['type'] == 'Expense':
                proj_balances[sch['from_account_id']] = float(proj_balances.get(sch['from_account_id'], 0)) - amt
            elif sch['type'] == 'Income':
                proj_balances[sch['to_account_id']] = float(proj_balances.get(sch['to_account_id'], 0)) + amt
            elif sch['type'] == 'Transfer':
                proj_balances[sch['from_account_id']] = float(proj_balances.get(sch['from_account_id'], 0)) - amt
                proj_balances[sch['to_account_id']] = float(proj_balances.get(sch['to_account_id'], 0)) + amt
            
            simulated_log.append({
                "date": run_date,
                "desc": sch['description'],
                "amount": amt,
                "type": sch['type']
            })
            
            # Move to next occurrence
            if sch['frequency'] == 'Monthly':
                run_date += relativedelta(months=1)
            else:
                break # One-time only happens once
                
    return proj_balances, simulated_log

# --- 3. APP INTERFACE ---
st.title("💰 My Wealth Manager")

# Auto-Run
processed = run_scheduled_transactions()
if processed: st.toast(f"Processed {processed} items!", icon="🤖")

df_accounts = get_accounts()
account_map = dict(zip(df_accounts['name'], df_accounts['id']))
account_list = df_accounts['name'].tolist()

# TABS RE-ORDERED
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🔮 Projection", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW (The "Finance Stand") ---
with tab1:
    st.subheader("Current Net Worth")
    
    # Calculate Totals
    total_assets = df_accounts[df_accounts['type'].isin(['Bank', 'Cash', 'Investment'])]['balance'].sum()
    total_debt = df_accounts[df_accounts['type'].isin(['Credit Card'])]['balance'].sum()
    custodial_holdings = df_accounts[df_accounts['type'] == 'Custodial']['balance'].sum() 
    # Note: Custodial is usually negative (liability), meaning money in your bank belongs to others
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Net Worth (My Money)", f"${(total_assets + total_debt + custodial_holdings):,.2f}") 
    c2.metric("Actual Bank Balance", f"${total_assets:,.2f}")
    c3.metric("Credit Card Debt", f"${total_debt:,.2f}")

    st.divider()
    st.caption("Account Breakdown")
    st.dataframe(df_accounts[['name', 'type', 'balance']], hide_index=True, use_container_width=True)

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    # Updated Types to include 'Custodial In'
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In", "Refund"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", datetime.today())
        amt = c2.number_input("Amount", min_value=0.01)
        
        f_acc, t_acc = None, None
        
        if t_type == "Expense":
            f_acc = st.selectbox("Paid From", account_list)
        elif t_type in ["Income", "Refund"]:
            t_acc = st.selectbox("Deposit To", account_list)
        elif t_type == "Transfer":
            c_a, c_b = st.columns(2)
            f_acc = c_a.selectbox("From", account_list)
            t_acc = c_b.selectbox("To", account_list)
        elif t_type == "Custodial In":
            st.info("Someone banked in money to you (Liability increases, Bank Balance increases)")
            c_a, c_b = st.columns(2)
            # Find custodial accounts
            cust_accs = df_accounts[df_accounts['type'] == 'Custodial']['name'].tolist()
            bank_accs = df_accounts[df_accounts['type'] == 'Bank']['name'].tolist()
            
            f_acc = c_a.selectbox("Custodial Source (Liability)", cust_accs if cust_accs else account_list)
            t_acc = c_b.selectbox("Bank Account Received", bank_accs if bank_accs else account_list)

        desc = st.text_input("Description")
        
        if st.form_submit_button("Submit"):
            add_transaction(date, amt, desc, t_type, account_map.get(f_acc), account_map.get(t_acc))
            st.success("Saved!")
            st.rerun()

# --- TAB 3: PROJECTION (The Crystal Ball) ---
with tab3:
    st.subheader("🔮 Future Balance Projector")
    st.write("See if you have enough money for upcoming bills.")
    
    target_date = st.date_input("Project Until Date", datetime.today() + timedelta(days=30))
    
    if st.button("Calculate Projection"):
        final_balances, logs = get_projection(target_date, df_accounts)
        
        # Display Results
        st.write(f"### Projected Balances on {target_date}")
        
        # Create a nice comparison table
        proj_data = []
        for index, row in df_accounts.iterrows():
            curr = row['balance']
            fut = final_balances.get(row['id'], curr)
            diff = fut - curr
            proj_data.append({
                "Account": row['name'],
                "Current": f"${curr:,.2f}",
                "Projected": f"${fut:,.2f}",
                "Change": f"{diff:+,.2f}"
            })
            
        st.dataframe(pd.DataFrame(proj_data), hide_index=True, use_container_width=True)
        
        if logs:
            with st.expander("See scheduled items included in this calculation"):
                st.dataframe(pd.DataFrame(logs))
        else:
            st.info("No scheduled transactions found for this period.")

# --- TAB 4: SCHEDULE ---
with tab4:
    st.subheader("Manage Recurring / Future Payments")
    # (Simplified for brevity, same logic as before)
    with st.form("sch_form"):
        desc = st.text_input("Desc")
        c1, c2, c3 = st.columns(3)
        amt = c1.number_input("Amount", min_value=0.01)
        freq = c2.selectbox("Freq", ["Monthly", "One-Time"])
        s_date = c3.date_input("Start Date")
        
        s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
        s_f, s_t = None, None
        if s_type == "Expense": s_f = st.selectbox("From", account_list)
        elif s_type == "Income": s_t = st.selectbox("To", account_list)
        elif s_type == "Transfer": 
            s_f = st.selectbox("From", account_list, key="sf") 
            s_t = st.selectbox("To", account_list, key="st")
            
        if st.form_submit_button("Add Schedule"):
            from_id = account_map.get(s_f)
            to_id = account_map.get(s_t)
            # Insert into DB
            supabase.table('schedule').insert({
                "description": desc, "amount": amt, "type": s_type, 
                "from_account_id": from_id, "to_account_id": to_id, 
                "frequency": freq, "next_run_date": str(s_date)
            }).execute()
            st.success("Scheduled!")
            st.rerun()
            
    # View Schedule
    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.dataframe(pd.DataFrame(upcoming)[['next_run_date', 'description', 'amount']])
        d_id = st.number_input("ID to Delete", min_value=0)
        if st.button("Delete Schedule Item"):
            supabase.table('schedule').delete().eq("id", d_id).execute()
            st.rerun()

# --- TAB 5: SETTINGS ---
with tab5:
    st.write("Create Account")
    with st.form("create_acc"):
        name = st.text_input("Name")
        type = st.selectbox("Type", ["Bank", "Credit Card", "Custodial", "Sinking Fund"])
        bal = st.number_input("Starting Balance")
        if st.form_submit_button("Create"):
            supabase.table('accounts').insert({"name": name, "type": type, "balance": bal}).execute()
            st.rerun()
