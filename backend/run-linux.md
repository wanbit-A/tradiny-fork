> Since I use Linux Mint (Ubuntu-based), I've faced errors for running the backend, so here's how to do it:
# 1. Ensure the python3-full package is installed
sudo apt update
sudo apt install python3-full

# 2. Navigate to your project directory and create a virtual environment named "venv"
cd /path/to/your/project
python3 -m venv venv

# 3. Activate the virtual environment
source venv/bin/activate

# 4. Now install packages safely using standard pip
pip install <package_name>
> In our case, it is:
pip3 install -r requirements.txt