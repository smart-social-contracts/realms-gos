export const idlFactory = ({ IDL }) => {
  return IDL.Service({
    'read' : IDL.Func([IDL.Text, IDL.Text], [IDL.Text], ['query']),
    'run' : IDL.Func([IDL.Text, IDL.Text], [IDL.Text], []),
  });
};
export const init = ({ IDL }) => { return []; };
