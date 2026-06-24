import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import seaborn as stns

st.set_page_config(page_title="Bandwidth Usage Analyzer", layout="wide")

# Regular expression to extract the date range from PDF text
# e.g., "Date Range: 2026-06-20 - 2026-06-20"
DATE_PATTERN = re.compile(r"Date Range:\s*(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

def parse_bandwidth_str(val_str):
    if not isinstance(val_str, str):
        return val_str
    # Remove commas and convert to float
    clean_str = val_str.replace(',', '').strip()
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

def process_pdf(file):
    """Parses a single PDF and returns a list of dictionaries with the data."""
    extracted_data = []
    report_date = None
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # 1. Extract text to find the date range and data rows
            text = page.extract_text()
            if not text:
                continue
                
            if not report_date:
                match = DATE_PATTERN.search(text)
                if match:
                    # Use the end date of the range
                    date_str = match.group(2)
                    try:
                        report_date = pd.to_datetime(date_str).date()
                    except:
                        pass
            
            # Regex to match the data rows (User, Bandwidth, Requests, Browse Time)
            # This updated regex handles optional Requests and Time, and allows Time as MM:SS or H:MM:SS
            row_pattern = re.compile(r"^(.*?)\s+([\d,]+)(?:\s+([\d,]+))?(?:\s+(\d{1,4}:\d{2}(?::\d{2})?))?$")
            
            page_extracted_data = []
            pending_user_name = ""
            
            for line in text.split('\n'):
                line = line.strip()
                
                # Ignore empty lines, headers, or totals
                if not line or "Total:" in line or "User" in line or "Bandwidth" in line or "Date Range" in line or "Requests" in line:
                    continue
                
                # If we have a pending user name from the previous line and this line looks like just numbers/time
                if pending_user_name and re.match(r"^[\d,\s:]+$", line):
                    line = pending_user_name + " " + line
                    pending_user_name = ""
                    
                row_match = row_pattern.match(line)
                if row_match:
                    user_val = row_match.group(1).strip()
                    bw_val = row_match.group(2).strip()
                    
                    if not user_val or user_val.lower() == 'user' or not re.search(r'[a-zA-Z]', user_val):
                        # Invalid user name (e.g. it's just numbers), skip
                        continue
                        
                    bandwidth_kb = parse_bandwidth_str(bw_val)
                    
                    page_extracted_data.append({
                        'Date': report_date,
                        'User': user_val,
                        'Bandwidth (KB)': bandwidth_kb,
                        'Source File': file.name
                    })
                    pending_user_name = "" # Reset just in case
                else:
                    # If it didn't match, check if it's just text (a wrapped user name)
                    if not re.search(r'\d', line) or (len(line) > 5 and not re.search(r'\s+[\d,]+\s+', line) and "Page" not in line):
                        pending_user_name = line
                    elif len(line) > 10 and not re.search(r"Page \d+", line):
                        if 'unparsed' not in st.session_state:
                            st.session_state.unparsed = []
                        st.session_state.unparsed.append(f"[{file.name}] {line}")
            
            extracted_data.extend(page_extracted_data)
            
            # If regex didn't find anything on this page, try fallback to table extraction
            if not page_extracted_data:
                tables = page.extract_tables()
                for table in tables:
                    if not table or not table[0]:
                        continue
                    
                    headers = [str(h).strip().replace('\n', ' ') if h else '' for h in table[0]]
                    if any("User" in h for h in headers) and any("Bandwidth" in h for h in headers):
                        user_idx = -1
                        bw_idx = -1
                        for i, h in enumerate(headers):
                            if "User" in h: user_idx = i
                            elif "Bandwidth" in h: bw_idx = i
                        
                        if user_idx != -1 and bw_idx != -1:
                            for row in table[1:]:
                                if row[0] and "Total:" in str(row[0]): continue
                                user_val = str(row[user_idx]).replace('\n', ' ').strip() if row[user_idx] else ""
                                bw_val = str(row[bw_idx]).replace('\n', ' ').strip() if row[bw_idx] else "0"
                                if not user_val: continue
                                extracted_data.append({
                                    'Date': report_date,
                                    'User': user_val,
                                    'Bandwidth (KB)': parse_bandwidth_str(bw_val),
                                    'Source File': file.name
                                })

    # If no date was found in the text, fallback to today's date for these records
    if report_date is None:
        st.warning(f"Could not find 'Date Range' in {file.name}. Defaulting to today's date.")
        report_date = datetime.now().date()
        for row in extracted_data:
            row['Date'] = report_date

    return extracted_data

st.title("📊 Bandwidth Usage Analyzer")
st.markdown("Upload your PDF bandwidth reports to find the top consumers. You can upload in batches; new files will be appended to your data.")

if 'parsed_files' not in st.session_state:
    st.session_state.parsed_files = set()
if 'all_data' not in st.session_state:
    st.session_state.all_data = []
if 'uploader_key' not in st.session_state:
    st.session_state.uploader_key = 0

st.sidebar.header("Data Management")
if st.sidebar.button("🗑️ Clear / Reset Data"):
    st.session_state.parsed_files = set()
    st.session_state.all_data = []
    st.session_state.unparsed = []
    st.session_state.uploader_key += 1
    st.rerun()

uploaded_files = st.file_uploader("Upload PDF Reports", type="pdf", accept_multiple_files=True, key=f"uploader_{st.session_state.uploader_key}")

if uploaded_files is not None:
    current_file_names = {f.name for f in uploaded_files}
    
    # Sync: Remove data for files that were deleted from the uploader
    if st.session_state.all_data:
        st.session_state.all_data = [row for row in st.session_state.all_data if row['Source File'] in current_file_names]
        st.session_state.parsed_files = st.session_state.parsed_files.intersection(current_file_names)
    if 'unparsed' in st.session_state:
        st.session_state.unparsed = [line for line in st.session_state.unparsed if any(f"[{f}]" in line for f in current_file_names)]
    
    # Parse new files
    new_files = [f for f in uploaded_files if f.name not in st.session_state.parsed_files]
    if new_files:
        with st.spinner("Parsing new PDFs..."):
            for file in new_files:
                data = process_pdf(file)
                st.session_state.all_data.extend(data)
                st.session_state.parsed_files.add(file.name)

if not st.session_state.all_data:
    if uploaded_files:
        st.error("No valid table data found in the uploaded PDFs.")
else:
    df = pd.DataFrame(st.session_state.all_data)
    
    # Convert Date to datetime for filtering
    df['Date'] = pd.to_datetime(df['Date'])
    
    st.success(f"Currently analyzing {len(df)} records from {len(st.session_state.parsed_files)} file(s).")
        
    # Determine the maximum date in the dataset to use as the reference point
    max_date = df['Date'].max()
    
    st.sidebar.header("Filter Options")
    st.sidebar.write(f"**Latest Report Date:** {max_date.strftime('%Y-%m-%d')}")
    
    # User input for 'last N days'
    last_n_days = st.sidebar.number_input("Analyze Last N Days", min_value=1, max_value=365, value=7, step=1)
    
    # Calculate start date based on max_date and user input
    start_date = max_date - pd.Timedelta(days=last_n_days - 1)
    
    st.sidebar.write(f"**Analyzing From:** {start_date.strftime('%Y-%m-%d')}")
    
    # Filter the dataframe
    filtered_df = df[df['Date'] >= start_date].copy()
    
    if filtered_df.empty:
        st.warning("No data available for the selected date range.")
    else:
        # Calculate daily ranks for each user within each source file
        filtered_df['Daily_Rank'] = filtered_df.groupby('Source File')['Bandwidth (KB)'].rank(ascending=False, method='min').astype(int)
        
        # Group by user to get total bandwidth and frequency
        user_stats = filtered_df.groupby('User').agg(
            Total_Bandwidth_KB=('Bandwidth (KB)', 'sum'),
            Frequency=('Source File', 'nunique')
        ).reset_index()
        user_stats = user_stats.rename(columns={'Total_Bandwidth_KB': 'Bandwidth (KB)'})
        user_totals = user_stats.sort_values(by='Bandwidth (KB)', ascending=False)
        
        # Convert KB to GB for readability
        user_totals['Bandwidth (GB)'] = user_totals['Bandwidth (KB)'] / (1024 * 1024)
        
        # Create a label for the chart that includes the frequency
        user_totals['User_Label'] = user_totals['User'] + " (" + user_totals['Frequency'].astype(str) + "x)"
        
        # Top User
        top_user = user_totals.iloc[0]
        
        st.subheader("🏆 Top Consumer")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(label="Highest Bandwidth User", value=top_user['User'])
        with col2:
            st.metric(label="Total Bandwidth (GB)", value=f"{top_user['Bandwidth (GB)']:.2f} GB")
        with col3:
            st.metric(label="Appearance Frequency", value=f"{top_user['Frequency']} reports")
            
        st.divider()
        st.subheader("🏅 Detailed User Ranks")
        
        # Selectbox to let the user pick who to see ranks for, defaulting to top consumer
        selected_user = st.selectbox(
            "Select a user to see their rank in each individual PDF report:", 
            user_totals['User'].tolist()
        )
        
        selected_user_ranks = filtered_df[filtered_df['User'] == selected_user][['Source File', 'Date', 'Daily_Rank']].sort_values('Date')
        
        rank_details = []
        for _, r in selected_user_ranks.iterrows():
            date_str = r['Date'].strftime('%Y-%m-%d')
            rank_details.append(f"Rank **#{r['Daily_Rank']}** on {date_str} ({r['Source File']})")
            
        for detail in rank_details:
            st.markdown(f"- {detail}")
            
        st.divider()
        
        st.subheader("📈 Top 10 Users by Bandwidth")
        st.markdown("*Note: 'Nx' next to the name indicates how many days/reports the user appeared in.*")
        
        # Bar Chart
        top_10 = user_totals.head(10)
        fig, ax = plt.subplots(figsize=(10, 6))
        stns.barplot(data=top_10, x='Bandwidth (GB)', y='User_Label', ax=ax, palette='viridis')
        ax.set_title(f"Top 10 Users (Last {last_n_days} days)")
        ax.set_xlabel("Bandwidth (GB)")
        ax.set_ylabel("User (Frequency)")
        st.pyplot(fig)
        
        st.subheader("📋 Aggregated Data")
        display_df = user_totals[['User', 'Bandwidth (GB)', 'Bandwidth (KB)', 'Frequency']].copy()
        display_df['Bandwidth (GB)'] = display_df['Bandwidth (GB)'].round(2)
        
        def get_all_ranks_str(user):
            user_ranks = filtered_df[filtered_df['User'] == user].sort_values('Date')
            return ", ".join([f"#{r['Daily_Rank']}" for _, r in user_ranks.iterrows()])
            
        def get_all_dates_str(user):
            user_ranks = filtered_df[filtered_df['User'] == user].sort_values('Date')
            return ", ".join([r['Date'].strftime('%Y-%m-%d') for _, r in user_ranks.iterrows()])
            
        display_df['Daily Ranks'] = display_df['User'].apply(get_all_ranks_str)
        display_df['Dates'] = display_df['User'].apply(get_all_dates_str)
        
        # Reset index to be sequential starting from 1
        display_df.index = range(1, len(display_df) + 1)
        
        # Download button for CSV
        csv_data = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Download Analyzed Data as CSV",
            data=csv_data,
            file_name="bandwidth_usage.csv",
            mime="text/csv"
        )
        
        st.dataframe(display_df, use_container_width=True)
        
        with st.expander("Show Raw Extracted Data"):
            st.dataframe(filtered_df)
            
        if 'unparsed' in st.session_state and st.session_state.unparsed:
            with st.expander("Show Skipped/Unparsed Lines (Debugging)"):
                st.warning("The following lines from the PDFs were not parsed because they didn't match the expected format:")
                for line in st.session_state.unparsed:
                    st.text(line)
