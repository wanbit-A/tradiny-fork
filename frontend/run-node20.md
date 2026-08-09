> Since some package named "canvas" doesn't work with Node 22, we are going to downgrade:
> Run these commands:
nvm install 20
nvm use 20
# Optional: save this requirement to the repo
echo "20" > .nvmrc

# Clean up previous failed attempts
rm -rf node_modules package-lock.json

# Reinstall and build
npm install
npm run build
npm start