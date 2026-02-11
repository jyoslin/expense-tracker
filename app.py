import streamlit as st
import hmac
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

# --- 1. SECURITY & SETUP ---
st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        if hmac.compare_digest(st.session_state["password"], st.secrets["APP_PASSWORD"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input("🔒 Please enter your password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("😕 Password incorrect")
    return False

if not check_password():
    st.stop()

# Connect to Supabase
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# --- 2. HELPER FUNCTIONS ---
def get_accounts():
    """Fetch accounts"""
    accounts = supabase.table('accounts').select("*").execute().data
    df = pd.DataFrame(accounts)
    if df.empty: return pd.DataFrame(columns=['id', 'name', 'type', 'balance', 'include_net_worth', 'is_liquid_asset', 'goal_amount', 'goal_date'])
    return df.sort_values(by=['name'])

def get_categories(type_filter=None):
    """Fetch categories"""
    query = supabase.table('categories').select("*")
    if type_filter:
        query = query.eq('type', type_filter)
    data = query.execute().data
    if not data: return []
    return sorted([item['name'] for item in data])

def update_balance(account_id, amount_change):
    if not account_id: return 
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def update_account_settings(id, include_nw, is_asset, goal_amt, goal_date):
    """Save account preferences"""
    data = {
        "include_net_worth": include_nw,
        "is_liquid_asset": is_asset,
        "goal_amount": goal_amt,
        "goal_date": str(goal_date) if goal_date else None
    }
    supabase.table('accounts').update(data).eq("id", id).execute()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id, category):
    # Record
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id, "category": category
    }).execute()

    # Update Balances
    if type == "Expense": update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]: update_balance(to_acc_id, amount)
    elif type in ["Transfer", "Custodial In"]:
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)
    elif type == "Custodial Out":
        if from_acc_id: update_balance(from_acc_id, -amount) 
        update_balance(to_acc_id, amount)

def run_scheduled_transactions():
    """Auto-run due transactions (ONLY if is_manual is False)"""
    today = datetime.today().date()
    # Fetch tasks due today or earlier that are AUTO (is_manual is false or null)
    tasks = supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', False).execute().data
    count = 0
    if tasks:
        for task in tasks:
            add_transaction(task['next_run_date'], task['amount'], f"🔄 {task['description']}", 
                          task['type'], task['from_account_id'], task['to_account_id'], "Recurring")
            
            # Reschedule or Delete
            if task['frequency'] == 'Monthly':
                next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
            else:
                supabase.table('schedule').delete().eq("id", task['id']).execute()
            count += 1
    return count

def get_due_manual_tasks():
    """Fetch manual reminders that are due"""
    today = datetime.today().date()
    return supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', True).execute().data

# --- 3. APP INTERFACE ---
st.title("💰 My Wealth Manager")

# 1. Run Auto-Bot
processed = run_scheduled_transactions()
if processed: st.toast(f"Processed {processed} auto-payments!", icon="🤖")

# 2. Check Manual Reminders
manual_due = get_due_manual_tasks()
if manual_due:
    st.warning(f"🔔 You have {len(manual_due)} manual transfers due!")

df_accounts = get_accounts()
account_map = dict(zip(df_accounts['name'], df_accounts['id']))
account_list = df_accounts['name'].tolist()

# Filter lists for specific logic
# Loan accounts should not be sources for expenses
non_loan_accounts = df_accounts[df_accounts['type'] != 'Loan']['name'].tolist()

# TABS
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW ---
with tab1:
    # --- MANUAL TASKS SECTION (New!) ---
    if manual_due:
        st.write("### 🔔 Pending Manual Transfers")
        for task in manual_due:
            with st.expander(f"Due: {task['next_run_date']} - {task['description']} (${task['amount']})", expanded=True):
                st.write(f"**Action:** {task['type']} of ${task['amount']}")
                if st.button("✅ I have done this transfer", key=f"done_{task['id']}"):
                    # 1. Record the transaction
                    add_transaction(date.today(), task['amount'], f"✅ {task['description']}", 
                                  task['type'], task['from_account_id'], task['to_account_id'], "Recurring")
                    
                    # 2. Update Schedule
                    if task['frequency'] == 'Monthly':
                        next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                        supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
                    else:
                        supabase.table('schedule').delete().eq("id", task['id']).execute()
                    
                    st.success("recorded and rescheduled!")
                    st.rerun()
        st.divider()

    st.subheader("Current Finance Stand")
    
    # Logic: Loans are liabilities (usually negative balance). Investments are Assets.
    # Net Worth = Assets (Bank + Cash + Investment) - Debts (Credit Card + Loan + Custodial)
    
    nw_accounts = df_accounts[df_accounts['include_net_worth'] == True]
    net_worth = nw_accounts['balance'].sum()
    
    asset_accounts = df_accounts[df_accounts['is_liquid_asset'] == True]
    total_liquid = asset_accounts['balance'].sum()
    
    c1, c2 = st.columns(2)
    c1.metric("Net Worth", f"${net_worth:,.2f}") 
    c2.metric("Liquid Bank Assets", f"${total_liquid:,.2f}")

    st.divider()
    
    # CATEGORY CHART
    st.subheader("📊 Spending by Category (Last 30 Days)")
    expenses = supabase.table('transactions').select("*").eq('type', 'Expense').order('date', desc=True).limit(50).execute().data
    if expenses:
        df_exp = pd.DataFrame(expenses)
        if 'category' in df_exp.columns and not df_exp.empty:
            cat_sum = df_exp.groupby('category')['amount'].sum().reset_index().sort_values('amount', ascending=False)
            
            col_chart, col_data = st.columns([2, 1])
            with col_chart:
                st.bar_chart(cat_sum.set_index('category'))
            with col_data:
                st.dataframe(cat_sum, hide_index=True, use_container_width=True)
    else:
        st.info("No recent expenses to show.")

    st.divider()
    
    # ACCOUNT HISTORY
    st.subheader("🔍 Account Details & History")
    selected_acc_name = st.selectbox("Select Account", account_list)
    
    if selected_acc_name:
        selected_acc_id = account_map[selected_acc_name]
        history = supabase.table('transactions').select("*") \
            .or_(f"from_account_id.eq.{selected_acc_id},to_account_id.eq.{selected_acc_id}") \
            .order('date', desc=True).limit(50).execute().data
            
        if history:
            st.dataframe(pd.DataFrame(history)[['date', 'category', 'description', 'amount', 'type', 'id']], hide_index=True, use_container_width=True)
            
            with st.expander("🗑️ Delete Transaction"):
                del_id = st.number_input("Transaction ID to Delete", min_value=0, step=1)
                if st.button("Delete Transaction"):
                    tx = supabase.table('transactions').select("*").eq('id', del_id).execute().data
                    if tx:
                        tx = tx[0]
                        if tx['type'] == "Expense": update_balance(tx['from_account_id'], tx['amount'])
                        elif tx['type'] in ["Income", "Refund"]: update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] in ["Transfer", "Custodial In"]:
                            update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] == "Custodial Out":
                            if tx['from_account_id']: update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                            
                        supabase.table('transactions').delete().eq('id', del_id).execute()
                        st.success("Deleted!")
                        st.rerun()

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In", "Custodial Out"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", datetime.today())
        
        # --- CUSTODIAL OUT ---
        if t_type == "Custodial Out":
            st.info("Paying back custodial money (Split Payment)")
            cust_acc = st.selectbox("Which Custodial Account?", df_accounts[df_accounts['type']=='Custodial']['name'])
            
            st.write("--- Sources ---")
            col_bank, col_cash = st.columns(2)
            with col_bank:
                # Can only pay from Non-Loan accounts
                bank_source = st.selectbox("Bank Source", df_accounts[df_accounts['type'].isin(['Bank', 'Cash'])]['name'])
                bank_amount = st.number_input("Amount from Bank", min_value=0.0, format="%.2f")
            with col_cash:
                cash_source_name = st.selectbox("Cash Source", ["Physical Wallet (Untracked)"] + account_list)
                cash_amount = st.number_input("Amount from Cash", min_value=0.0, format="%.2f")
            
            desc = st.text_input("Description")
            category = "Custodial" 
            
            if st.form_submit_button("Process Split Payment"):
                if bank_amount > 0:
                    add_transaction(date, bank_amount, f"{desc} (Bank)", "Custodial Out", account_map[bank_source], account_map[cust_acc], category)
                if cash_amount > 0:
                    cash_id = account_map.get(cash_source_name)
                    if not cash_id:
                        supabase.table('transactions').insert({
                            "date": str(date), "amount": cash_amount, "description": f"{desc} (Cash)", "type": "Custodial Out",
                            "to_account_id": account_map[cust_acc], "category": category
                        }).execute()
                        update_balance(account_map[cust_acc], cash_amount)
                    else:
                        add_transaction(date, cash_amount, f"{desc} (Cash)", "Custodial Out", cash_id, account_map[cust_acc], category)
                st.success("Saved!")
                st.rerun()

        # --- STANDARD TRANSACTIONS ---
        else:
            amt = c2.number_input("Amount", min_value=0.01)
            
            cat_options = get_categories("Income") if t_type == "Income" else get_categories("Expense")
            category = st.selectbox("Category", cat_options)
            
            f_acc, t_acc = None, None
            
            if t_type == "Expense": 
                # PREVENT LOANS FROM BEING SOURCE OF EXPENSE
                f_acc = st.selectbox("Paid From", non_loan_accounts)
            elif t_type in ["Income", "Refund"]: 
                t_acc = st.selectbox("Deposit To", account_list)
            elif t_type == "Transfer":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("From", non_loan_accounts) # Cant transfer FROM loan
                t_acc = c_b.selectbox("To", account_list) # Can transfer TO loan (repayment)
            elif t_type == "Custodial In":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("Custodial Source", df_accounts[df_accounts['type']=='Custodial']['name'])
                t_acc = c_b.selectbox("Bank Received", df_accounts[df_accounts['type']=='Bank']['name'])

            desc = st.text_input("Description")
            
            if st.form_submit_button("Submit"):
                add_transaction(date, amt, desc, t_type, account_map.get(f_acc), account_map.get(t_acc), category)
                st.success("Saved!")
                st.rerun()

# --- TAB 3: GOALS ---
with tab3:
    st.subheader("🎯 Goal Tracker")
    goals = df_accounts[df_accounts['type'] == 'Sinking Fund']
    
    for index, row in goals.iterrows():
        with st.expander(f"📌 {row['name']} (Current: ${row['balance']:,.2f})", expanded=True):
            goal_amt = row['goal_amount'] or 0
            if row['balance'] >= goal_amt and goal_amt > 0:
                st.success("🎉 GOAL ACHIEVED!")
                with st.form(f"rotate_goal_{row['id']}"):
                    new_goal = st.number_input("New Goal Amount", value=float(goal_amt))
                    new_date = st.date_input("New Deadline")
                    if st.form_submit_button("🔄 Rotate Goal"):
                        update_account_settings(row['id'], row['include_net_worth'], row['is_liquid_asset'], new_goal, new_date)
                        st.rerun()
            elif goal_amt > 0:
                shortfall = goal_amt - row['balance']
                progress = min(row['balance'] / goal_amt, 1.0)
                st.progress(progress)
                st.caption(f"Target: ${goal_amt:,.2f}")
                if row['goal_date']:
                    deadline = datetime.strptime(row['goal_date'], '%Y-%m-%d').date()
                    months_left = (deadline.year - date.today().year) * 12 + (deadline.month - date.today().month)
                    if months_left > 0:
                        st.info(f"💡 Save **${shortfall / months_left:,.2f} / month**")

# --- TAB 4: SCHEDULE ---
with tab4:
    st.subheader("Manage Future Payments")
    
    with st.expander("➕ Add New Schedule", expanded=True):
        with st.form("sch_form"):
            s_desc = st.text_input("Description (e.g. Rent, Investment)")
            c1, c2, c3 = st.columns(3)
            s_amount = c1.number_input("Amount", min_value=0.01)
            s_freq = c2.selectbox("Frequency", ["Monthly", "One-Time"])
            s_date = c3.date_input("Start Date", datetime.today())
            
            # MANUAL CHECKBOX
            s_manual = st.checkbox("🔔 Manual Reminder? (Tick this if you do the transfer manually, e.g. PayNow)", value=False)
            
            s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
            
            s_from, s_to = None, None
            if s_type == "Expense":
                s_from = st.selectbox("From Account", non_loan_accounts)
            elif s_type == "Income":
                s_to = st.selectbox("To Account", account_list)
            elif s_type == "Transfer":
                s_from = st.selectbox("From", non_loan_accounts, key="s_f")
                s_to = st.selectbox("To", account_list, key="s_t")
                
            if st.form_submit_button("Schedule It"):
                f_id = account_map.get(s_from)
                t_id = account_map.get(s_to)
                supabase.table('schedule').insert({
                    "description": s_desc, "amount": s_amount, "type": s_type, 
                    "from_account_id": f_id, "to_account_id": t_id, 
                    "frequency": s_freq, "next_run_date": str(s_date),
                    "is_manual": s_manual
                }).execute()
                st.success("Scheduled!")
                st.rerun()

    st.divider()
    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.write("### 🗓️ Upcoming Items")
        # Format for display
        df_up = pd.DataFrame(upcoming)
        df_up['Manual?'] = df_up['is_manual'].apply(lambda x: "🔔 Manual" if x else "🤖 Auto")
        st.dataframe(df_up[['next_run_date', 'description', 'amount', 'frequency', 'Manual?', 'id']], hide_index=True)
        
        del_sch_id = st.number_input("Schedule ID to Delete", min_value=0)
        if st.button("Delete Schedule Item"):
            supabase.table('schedule').delete().eq("id", del_sch_id).execute()
            st.rerun()

# --- TAB 5: SETTINGS ---
with tab5:
    st.subheader("🔧 Configuration")
    
    # 1. MANAGE CATEGORIES
    with st.expander("📂 Manage Categories", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.write("**Add Category**")
            new_cat = st.text_input("Name", key="new_cat_name")
            new_cat_type = st.selectbox("Type", ["Expense", "Income"], key="new_cat_type")
            if st.button("Add"):
                supabase.table('categories').insert({"name": new_cat, "type": new_cat_type}).execute()
                st.success(f"Added {new_cat}")
                st.rerun()
        with c2:
            st.write("**Delete Category**")
            all_cats = get_categories()
            if all_cats:
                del_cat = st.selectbox("Select to Delete", all_cats)
                if st.button("Delete"):
                    supabase.table('categories').delete().eq("name", del_cat).execute()
                    st.rerun()

    st.divider()

    # 2. CREATE ACCOUNT
    with st.expander("➕ Add New Account", expanded=False):
        with st.form("create_acc"):
            new_name = st.text_input("Name")
            # UPDATED TYPES LIST
            new_type = st.selectbox("Type", ["Bank", "Credit Card", "Custodial", "Sinking Fund", "Cash", "Loan", "Investment"])
            initial_bal = st.number_input("Starting Balance (Negative for Loan)", value=0.0)
            
            st.write("--- Goal Settings (Sinking Funds Only) ---")
            new_goal = st.number_input("Goal Amount", value=0.0)
            new_date = st.date_input("Goal Deadline", value=None)
            
            if st.form_submit_button("Create Account"):
                data = {
                    "name": new_name, "type": new_type, "balance": initial_bal,
                    "include_net_worth": True, "is_liquid_asset": True if new_type in ['Bank', 'Cash', 'Investment'] else False,
                    "goal_amount": new_goal if new_type == "Sinking Fund" else 0,
                    "goal_date": str(new_date) if new_type == "Sinking Fund" else None
                }
                supabase.table('accounts').insert(data).execute()
                st.success(f"Created {new_name}!")
                st.rerun()

    st.divider()
    
    # 3. EDIT ACCOUNT
    edit_acc = st.selectbox("Edit Existing Account", account_list)
    if edit_acc:
        row = df_accounts[df_accounts['name'] == edit_acc].iloc[0]
        
        with st.form("edit_settings"):
            c1, c2 = st.columns(2)
            inc_nw = c1.checkbox("Include in Net Worth?", value=row['include_net_worth'])
            is_liq = c2.checkbox("Is Actual Bank Asset?", value=row['is_liquid_asset'])
            
            g_amt = 0.0
            g_date = None
            if row['type'] == 'Sinking Fund':
                st.divider()
                st.write("Goal Settings")
                g_amt = st.number_input("Goal Amount", value=float(row['goal_amount'] or 0))
                g_date = st.date_input("Goal Deadline", value=datetime.strptime(row['goal_date'], '%Y-%m-%d') if row['goal_date'] else None)
            
            if st.form_submit_button("Save Changes"):
                update_account_settings(row['id'], inc_nw, is_liq, g_amt, g_date)
                st.success("Updated!")
                st.rerun()
