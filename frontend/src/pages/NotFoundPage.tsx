import { Link } from "react-router-dom";
import { PageHeader } from "../components/Layout";

export function NotFoundPage() {
  return (
    <div className="page">
      <PageHeader title="Page not found" />
      <p>
        There is nothing at this address. Go to the <Link to="/">dashboard</Link> or the{" "}
        <Link to="/incidents">incident list</Link>.
      </p>
    </div>
  );
}
