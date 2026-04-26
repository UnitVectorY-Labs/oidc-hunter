This is an LLM based agent that discovers public OIDC endpoints for https://github.com/UnitVectorY-Labs/jwks-catalog and maintains a reviewable candidate set for catalog inclusion.

This is implemented in Python using Google's ADK framework.

Whenever any update is made to the implementation, be sure to update the relevant documentation files in the docs/ folder.

This application uses https://github.com/UnitVectorY-Labs/gitrepoforge to manage files contents automatically using the .gitrepoforge file in addition to some external configuration files. The files managed by gitrepoforge are listed in `.managedfiles` and modifying these files directly is highly discouraged as these changes will be overwritten by gitrepoforge.  But if absolutely unavoidable you can modify these files leaving a note that the file's upstream template needs to be updated as well.
